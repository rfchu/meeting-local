"""Prepare a verified model directory without changing the active model root."""
import errno
import hashlib
import json
import os
import shutil
import stat
import struct
import zipfile
import zlib
import tempfile
from contextlib import ExitStack
from pathlib import Path
from threading import Event, Lock, Thread

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .model_archive import MAX_CENTRAL_DIRECTORY, archive_chunks, archive_members, open_zip
from .model_download import download_url, download_zip
from .model_pack import ModelPack, ModelPackError, _open_entry, is_cloud_placeholder, load_candidate, verify_directory
from .paths import configured_model_root, model_root, set_activated_models

BLOCK_SIZE = 8 * 1024 * 1024
BUSY_PHASES = {'copying', 'validating', 'downloading'}


class DirectoryRequest(BaseModel):
    directory: str = Field(min_length=1, max_length=4096)


class ArchiveRequest(BaseModel):
    archive: str = Field(min_length=1, max_length=4096)


class DownloadRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)


class DirectoryImport:
    def __init__(self, root: Path, pack: ModelPack):
        self.root = root
        self.pack = pack
        self.lock = Lock()
        self.cancelled = Event()
        self.total = sum(file.size for file in pack.files)
        key = json.dumps([(str(file.path), file.size, file.sha256) for file in
                          sorted(pack.files, key=lambda file: str(file.path))])
        self.ready = root / f'{pack.version}-{hashlib.sha256(key.encode()).hexdigest()}'
        self.status = self.initial_status()

    def initial_status(self):
        return {'phase': 'idle', 'version': self.pack.version, 'total_bytes': self.total,
                'copied_bytes': 0, 'downloaded_bytes': 0, 'download_total_bytes': None,
                'available_bytes': None, 'reused': False,
                'cancel_requested': False, 'error_code': None, 'error_path': None}

    def snapshot(self):
        with self.lock:
            return self.status.copy()

    def start(self, source: Path | str, *, archive=False):
        with self.lock:
            if self.status['phase'] in BUSY_PHASES:
                raise HTTPException(409, '已有模型包导入正在进行')
            self.cancelled.clear()
            phase = 'downloading' if isinstance(source, str) else 'copying'
            self.status = self.initial_status() | {'phase': phase}
            Thread(target=self.run, args=(source, archive), daemon=True).start()

    def cancel(self):
        with self.lock:
            if self.status['phase'] not in BUSY_PHASES:
                raise HTTPException(409, '当前没有可取消的导入')
            self.cancelled.set()
            self.status['cancel_requested'] = True

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise ModelPackError('cancelled')

    def run(self, source: Path | str, archive=False):
        try:
            self.prepare(source, archive)
            return
        except ModelPackError as error:
            result = {'phase': 'cancelled'} if error.code == 'cancelled' else {
                'phase': 'failed', 'error_code': error.code, 'error_path': error.path}
        except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, struct.error,
                UnicodeError, NotImplementedError):
            result = {'phase': 'failed', 'error_code': 'invalid_archive'}
        except OSError as error:
            code = 'no_space' if error.errno in (errno.ENOSPC, errno.EDQUOT) else 'unreadable'
            result = {'phase': 'failed', 'error_code': code, 'error_path': error.filename}
        with self.lock:
            if self.cancelled.is_set():
                result = {'phase': 'cancelled'}
            self.status.update(result, cancel_requested=False)

    def prepare(self, source: Path | str, archive=False):
        remote = isinstance(source, str)
        active = configured_model_root()
        staging = self.root.resolve()
        if staging.is_relative_to(active) or active.is_relative_to(staging):
            raise ModelPackError('active_root_overlap')
        self.root.mkdir(mode=0o700, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ModelPackError('unsafe_staging')

        free = shutil.disk_usage(self.root).free
        with self.lock:
            self.status['available_bytes'] = free
        if not (archive or remote) and os.path.lexists(self.ready):
            with self.lock:
                self.status['phase'] = 'validating'
            try:
                verify_directory(self.ready, self.pack, check_cancelled=self.check_cancelled)
            except ModelPackError as error:
                if error.code == 'cancelled' or self.ready / 'models' == model_root():
                    raise
                with self.lock:
                    self.status['phase'] = 'copying'
            else:
                with self.lock:
                    self.check_cancelled()
                    self.status.update(phase='ready', copied_bytes=self.total,
                                       reused=True, cancel_requested=False)
                return
        if free < self.total:
            raise ModelPackError('no_space')

        with tempfile.TemporaryDirectory(prefix='.copy-', dir=self.root) as temp:
            stage = Path(temp) / 'payload'
            stage.mkdir(mode=0o700)
            if remote:
                if free == self.total:
                    raise ModelPackError('no_space')
                incoming = Path(temp) / 'download.zip'
                # Reserve the full expansion size while the downloaded ZIP also exists.
                archive_limit = self.total + self.total // 100 + 65536 * len(self.pack.files) + MAX_CENTRAL_DIRECTORY
                download_space = free - self.total
                try:
                    download_zip(source, incoming, min(download_space, archive_limit),
                                 self.check_cancelled, self.report_download)
                except ModelPackError as error:
                    if error.code == 'download_limit' and download_space < archive_limit:
                        raise ModelPackError('no_space') from error
                    raise
                with self.lock:
                    self.status['phase'] = 'copying'
                self.unpack(incoming, stage)
                incoming.unlink()
            elif archive:
                self.unpack(source, stage)
            else:
                source_fd = _open_entry(source, source, directory=True)
                try:
                    # Accept either a package root or the models directory itself.
                    prefix = 0 if 'models' in os.listdir(source_fd) else 1
                    for file in self.pack.files:
                        self.copy_file(source_fd, prefix, stage, file)
                finally:
                    os.close(source_fd)
            with self.lock:
                self.status['phase'] = 'validating'
            verify_directory(stage, self.pack, check_cancelled=self.check_cancelled)
            reused = os.path.lexists(self.ready)
            if reused:
                try:
                    verify_directory(self.ready, self.pack, check_cancelled=self.check_cancelled)
                except ModelPackError as error:
                    if error.code == 'cancelled' or self.ready / 'models' == model_root():
                        raise
                    reused = False
            with self.lock:
                self.check_cancelled()
                if not reused:
                    if os.path.lexists(self.ready):
                        self.ready.rename(Path(temp) / 'invalid')
                    stage.rename(self.ready)
                self.status.update(phase='ready', reused=reused, cancel_requested=False)

    def report_download(self, downloaded, total):
        with self.lock:
            self.status.update(downloaded_bytes=downloaded, download_total_bytes=total)

    def copy_file(self, source_fd, prefix, stage, file):
        self.check_cancelled()
        path = str(file.path)
        parts = file.path.parts[prefix:]
        with ExitStack() as stack:
            parent = source_fd
            for segment in parts[:-1]:
                parent = _open_entry(segment, path, parent, directory=True)
                stack.callback(os.close, parent)
            try:
                info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError as error:
                raise ModelPackError('missing', path) from error
            self.check_source(info, file)
            fd = _open_entry(parts[-1], path, parent)
            stack.callback(os.close, fd)
            self.check_source(os.fstat(fd), file)
            def chunks():
                remaining = file.size
                while remaining:
                    self.check_cancelled()
                    chunk = os.read(fd, min(BLOCK_SIZE, remaining))
                    if not chunk:
                        raise ModelPackError('size_mismatch', path)
                    remaining -= len(chunk)
                    yield chunk
                if os.read(fd, 1):
                    raise ModelPackError('size_mismatch', path)

            self.write_file(stage / file.path, chunks())

    def unpack(self, source: Path, stage: Path):
        if source.suffix.lower() != '.zip':
            raise ModelPackError('unsupported_archive')
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ModelPackError('unsafe_entry', source.name)
        if is_cloud_placeholder(info):
            raise ModelPackError('cloud_placeholder', source.name)
        fd = _open_entry(source, source)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ModelPackError('unsafe_entry', source.name)
            if is_cloud_placeholder(info):
                raise ModelPackError('cloud_placeholder', source.name)
            with os.fdopen(fd, 'rb', closefd=False) as stream, open_zip(stream) as archive:
                members = archive_members(archive, self.pack)
                for file in self.pack.files:
                    self.check_cancelled()
                    member = members[str(file.path)]
                    self.write_file(stage / file.path, archive_chunks(archive, member, self.check_cancelled))
        finally:
            os.close(fd)

    def write_file(self, target, chunks):
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            for chunk in chunks:
                output.write(chunk)
                with self.lock:
                    self.status['copied_bytes'] += len(chunk)
            output.flush()
            os.fsync(output.fileno())

    @staticmethod
    def check_source(info, file):
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ModelPackError('unsafe_entry', str(file.path))
        if is_cloud_placeholder(info):
            raise ModelPackError('cloud_placeholder', str(file.path))
        if info.st_size != file.size:
            raise ModelPackError('size_mismatch', str(file.path))


def register_model_import(app, data: Path, *, capture_busy=lambda: False):
    try:
        pack = load_candidate(os.getenv('TINGJI_APP_VERSION', '0.4.0'))
    except (ModelPackError, OSError, ValueError):
        importer = None
    else:
        importer = DirectoryImport(data / 'model-imports', pack)

    activation = None
    activated_root = None
    if importer and os.getenv('TINGJI_PORTABLE_SERVICE') == '1':
        from .model_activation import ModelActivation
        activation = ModelActivation(data, importer.ready, pack)
        def boot_models():
            nonlocal activated_root
            activated_root = activation.boot(capture_busy())
            set_activated_models(activated_root)
        app.state.boot_models = boot_models

    def status():
        current = service()
        result = current.snapshot()
        result['activation_supported'] = activation is not None
        if activation:
            result.update(activation.status(activated_root))
            result['can_activate'] = current.ready.is_dir() and result['phase'] not in BUSY_PHASES
        return result

    def service():
        if importer is None:
            raise HTTPException(503, '当前 App 没有兼容的模型包清单')
        return importer

    @app.get('/api/runtime/model-import')
    def model_import_status():
        return JSONResponse(status(), headers={'Cache-Control': 'no-store'})

    @app.post('/api/runtime/model-import', status_code=202)
    def start_model_import(body: DirectoryRequest):
        path = Path(body.directory)
        if not path.is_absolute() or '\0' in body.directory or '..' in path.parts:
            raise HTTPException(422, '请输入本机目录的绝对路径')
        service().start(path)
        return status()

    @app.post('/api/runtime/model-import/archive', status_code=202)
    def start_model_archive_import(body: ArchiveRequest):
        path = Path(body.archive)
        if not path.is_absolute() or '\0' in body.archive or '..' in path.parts:
            raise HTTPException(422, '请输入本机 ZIP 压缩包的绝对路径')
        service().start(path, archive=True)
        return status()

    @app.post('/api/runtime/model-import/download', status_code=202)
    def start_model_download(body: DownloadRequest):
        try:
            download_url(body.url)
        except ModelPackError as error:
            raise HTTPException(422, '请输入有效的 HTTPS 模型包地址') from error
        service().start(body.url)
        return status()

    @app.post('/api/runtime/model-import/activate', status_code=202)
    def activate_model_import():
        current = service()
        if not activation:
            raise HTTPException(409, '仅独立版支持切换模型包')
        with current.lock:
            if current.status['phase'] in BUSY_PHASES or not current.ready.is_dir():
                raise HTTPException(409, '请等待模型包完成校验')
            try:
                activation.request()
            except OSError as error:
                raise HTTPException(503, '无法保存模型启用请求') from error
        return status()

    @app.delete('/api/runtime/model-import', status_code=202)
    def cancel_model_import():
        service().cancel()
        return status()
