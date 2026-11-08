"""Read-only validation of staged model files against the bundled candidate catalog."""
import errno
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ModelPackError(ValueError):
    def __init__(self, code: str, path: str = ''):
        self.code = code
        self.path = path
        super().__init__(f'{code}: {path}' if path else code)


class ManifestRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class Compatibility(ManifestRecord):
    min: str
    before: str


class FileRecord(ManifestRecord):
    path: str
    bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class Component(ManifestRecord):
    capability: str = Field(pattern=r'^[a-z][a-z0-9_]*$')
    source: str
    license_evidence: str
    files: list[FileRecord] = Field(min_length=1)


class Manifest(ManifestRecord):
    schema_version: int
    pack_version: str
    app_versions: Compatibility
    components: list[Component] = Field(min_length=1)


@dataclass(frozen=True)
class ModelFile:
    path: PurePosixPath
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelComponent:
    capability: str
    source: str
    license_evidence: str
    files: tuple[ModelFile, ...]


@dataclass(frozen=True)
class ModelPack:
    version: str
    components: tuple[ModelComponent, ...]

    @property
    def files(self) -> tuple[ModelFile, ...]:
        return tuple(file for component in self.components for file in component.files)


def _version(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r'(0|[1-9][0-9]{0,8})(\.(0|[1-9][0-9]{0,8})){2}', value):
        raise ModelPackError('invalid_version', value)
    return tuple(map(int, value.split('.')))


def parse_manifest(raw: object, app_version: str) -> ModelPack:
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as error:
        raise ModelPackError('invalid_manifest') from error
    if manifest.schema_version != 1:
        raise ModelPackError('unsupported_schema')
    _version(manifest.pack_version)
    lower, upper = _version(manifest.app_versions.min), _version(manifest.app_versions.before)
    if lower >= upper:
        raise ModelPackError('invalid_manifest')
    if not lower <= _version(app_version) < upper:
        raise ModelPackError('incompatible_app', app_version)

    files = {}
    directories = {}
    capabilities = set()
    components = []
    for component in manifest.components:
        if component.capability in capabilities:
            raise ModelPackError('duplicate_capability', component.capability)
        capabilities.add(component.capability)
        for url in (component.source, component.license_evidence):
            try:
                parts = urlsplit(url)
                if parts.scheme != 'https' or not parts.hostname or parts.username is not None:
                    raise ValueError
            except ValueError as error:
                raise ModelPackError('invalid_source', component.capability) from error
        component_files = []
        for entry in component.files:
            segments = entry.path.split('/')
            path = PurePosixPath(entry.path)
            if (segments[0] != 'models' or len(segments) < 2
                    or any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', part)
                           or part.endswith('.') for part in segments)
                    or path.suffix not in {'.bin', '.onnx', '.safetensors', '.json', '.txt', '.md'}):
                raise ModelPackError('unsafe_path', entry.path)
            key = entry.path.lower()
            if key in files or key in directories:
                raise ModelPackError('duplicate_path', entry.path)
            for parent in path.parents:
                if parent == PurePosixPath('.'):
                    break
                folded = str(parent).lower()
                if folded in files or directories.setdefault(folded, parent) != parent:
                    raise ModelPackError('duplicate_path', str(parent))
            model_file = ModelFile(path, entry.bytes, entry.sha256)
            files[key] = model_file
            component_files.append(model_file)
        components.append(ModelComponent(component.capability, component.source,
                                         component.license_evidence, tuple(component_files)))
    return ModelPack(manifest.pack_version, tuple(components))


def load_candidate(app_version: str) -> ModelPack:
    catalog = Path(__file__).with_name('model-pack-candidate.json')
    return parse_manifest(json.loads(catalog.read_text(encoding='utf-8')), app_version)


def _open_entry(name, path, parent_fd=None, *, directory=False):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if directory:
        flags |= os.O_DIRECTORY
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as error:
        code = {errno.ENOENT: 'missing', errno.ELOOP: 'unsafe_entry',
                errno.ENOTDIR: 'unsafe_entry'}.get(error.errno, 'unreadable')
        raise ModelPackError(code, str(path)) from error


def is_cloud_placeholder(info: os.stat_result) -> bool:
    # Darwin's SF_DATALESS marks a cloud placeholder even with a logical size.
    return sys.platform == 'darwin' and bool(info.st_flags & 0x40000000)


def verify_directory(root: Path, pack: ModelPack, *,
                     check_cancelled: Callable[[], None] = lambda: None) -> int:
    """Verify a private staging root with the existing models/ layout."""
    tree = {}
    for file in pack.files:
        directory = tree
        for part in file.path.parts[:-1]:
            directory = directory.setdefault(part, {})
        directory[file.path.name] = file

    def verify_tree(fd, expected, prefix):
        for name, entry in expected.items():
            check_cancelled()
            path = prefix / name
            if isinstance(entry, dict):
                child = _open_entry(name, path, fd, directory=True)
                try:
                    verify_tree(child, entry, path)
                finally:
                    os.close(child)
                continue
            file = _open_entry(name, path, fd)
            try:
                info = os.fstat(file)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ModelPackError('unsafe_entry', str(path))
                if is_cloud_placeholder(info):
                    raise ModelPackError('cloud_placeholder', str(path))
                if info.st_size != entry.size:
                    raise ModelPackError('size_mismatch', str(path))
                digest = hashlib.sha256()
                remaining = entry.size
                try:
                    while remaining and (chunk := os.read(file, min(8 * 1024 * 1024, remaining))):
                        check_cancelled()
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if remaining or os.read(file, 1):
                        raise ModelPackError('size_mismatch', str(path))
                except OSError as error:
                    raise ModelPackError('unreadable', str(path)) from error
                if digest.hexdigest() != entry.sha256:
                    raise ModelPackError('hash_mismatch', str(path))
            finally:
                os.close(file)
        try:
            extra = sorted(set(os.listdir(fd)) - expected.keys())
        except OSError as error:
            raise ModelPackError('unreadable', str(prefix)) from error
        if extra:
            raise ModelPackError('unexpected_entry', str(prefix / extra[0]))

    fd = _open_entry(root, root, directory=True)
    try:
        verify_tree(fd, tree, PurePosixPath())
    finally:
        os.close(fd)
    return sum(file.size for file in pack.files)
