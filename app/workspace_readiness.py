"""Metadata-only workspace checks shared by the UI and maintenance CLI."""
import os
import shutil
import sys
from pathlib import Path

from .backups import BackupError, is_dataless, preflight, require_space
from .providers import QwenMLX, WhisperCpp, LocalDiarization, LocalPunctuation


def path_status(path, *, executable=False, directory=False):
    path = Path(path).expanduser()
    try:
        info = path.stat()
        if is_dataless(info):
            return 'cloud_placeholder'
        if directory != path.is_dir():
            return 'inaccessible'
        if directory:
            for folder, dirs, files in os.walk(path, onerror=_raise):
                for name in dirs + files:
                    if is_dataless((Path(folder) / name).lstat()):
                        return 'cloud_placeholder'
        return 'inaccessible' if executable and not os.access(path, os.X_OK) else 'ready'
    except FileNotFoundError:
        return 'missing'
    except OSError:
        return 'inaccessible'


def _raise(error):
    raise error


def inspect(paths):
    whisper, qwen = WhisperCpp(), QwenMLX()
    entries = [
        ('repository', paths.repository, False),
        ('data', paths.data, False),
        ('backups', paths.backups, False),
        ('python', sys.executable, True),
        ('python_environment', paths.repository/'.venv', False),
        ('whisper_cli', whisper.binary, True),
        ('whisper_model', whisper.model, False),
        ('qwen_python', qwen.python, True),
        ('qwen_environment', qwen.runtime.parent, False),
        ('qwen_runtime', qwen.runtime, False),
        ('qwen_model', qwen.model, False),
        ('qwen_aligner', qwen.aligner, False),
        ('whisper_vad', whisper.vad, False),
        ('diarization_segmentation', LocalDiarization().segmentation, False),
        ('diarization_embedding', LocalDiarization().embedding, False),
        ('punctuation_model', LocalPunctuation().model, False),
    ]
    # Repository and backups can contain large historical trees. Check their roots only.
    result = []
    for name, path, executable in entries:
        path = Path(path)
        if name in ('repository', 'backups'):
            try:
                status = 'cloud_placeholder' if is_dataless(path.stat()) else 'ready' if path.is_dir() else 'inaccessible'
            except FileNotFoundError:
                status = 'missing'
            except OSError:
                status = 'inaccessible'
        else:
            status = path_status(path, executable=executable, directory=name in ('data', 'python_environment', 'qwen_environment', 'qwen_model', 'qwen_aligner'))
        if status == 'ready' and name in ('qwen_model', 'qwen_aligner') and not qwen.model_ready(path):
            status = 'incomplete'
        if status == 'ready' and name == 'data' and not (path/'meetings.sqlite3').is_file():
            status = 'incomplete'
        result.append({'id': name, 'path': str(path), 'state': status})
    try:
        free = shutil.disk_usage(paths.data).free
    except OSError:
        free = None
    return {'paths': result, 'free_bytes': free, 'python_version': sys.version.split()[0]}


def migration_plan(source, bundle, target):
    source, bundle, target = (Path(p).expanduser().resolve() for p in (source, bundle, target))
    roots = (source, bundle, target)
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
           for i, a in enumerate(roots) for b in roots[i + 1:]):
        raise BackupError('源数据、备份和恢复目标必须是相互独立的目录')
    if bundle.exists() or target.exists():
        raise BackupError('备份和恢复目标必须是尚不存在的新目录')
    info = preflight(source)
    parents = []
    for path in (bundle, target):
        parent = path.parent
        while not parent.exists():
            parent = parent.parent
        if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
            raise BackupError('备份或恢复目标的上级目录不可写')
        parents.append(parent)
    size = info['estimated_bytes']
    if parents[0].stat().st_dev == parents[1].stat().st_dev:
        require_space(parents[0], 2 * size)
    else:
        for parent in parents:
            require_space(parent, size)
    return {**info, 'source': str(source), 'backup': str(bundle), 'target': str(target),
            'ready': info['cloud_placeholder_count'] == 0,
            'switch_required': True, 'rollback_data_dir': str(source)}
