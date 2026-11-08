#!/usr/bin/env python3
"""Build a D01 relocation probe from this Mac's installed development inputs."""
import argparse
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACHO_64 = b'\xcf\xfa\xed\xfe'
FAT_MAGIC = {b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'}
SYSTEM_PREFIXES = ('/usr/lib/', '/System/Library/')
BREW_PREFIX = '/opt/homebrew/'


def run(*args):
    return subprocess.check_output(args, text=True)


def native_files(root):
    for path in sorted(root.rglob('*')):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as stream:
                magic = stream.read(4)
            # Java classes also use CAFEBABE. Native universal inputs are dylibs or extensions.
            if magic == MACHO_64 or magic in FAT_MAGIC and path.suffix in ('.so', '.dylib'):
                yield path


def relocate(bundle):
    libraries = bundle / 'lib'
    libraries.mkdir()
    pending = list(native_files(bundle))
    copied = {}
    origins = {}
    for path in pending:
        lines = run('/usr/bin/otool', '-l', str(path)).splitlines()
        changes = {}
        command = ''
        for line in lines:
            fields = line.strip().split(maxsplit=1)
            if len(fields) != 2:
                continue
            key, value = fields
            if key == 'cmd':
                command = value
            if key not in ('name', 'path'):
                continue
            reference = value.rsplit(' (offset ', 1)[0]
            if command == 'LC_RPATH' and reference.startswith('/') and not reference.startswith(SYSTEM_PREFIXES):
                changes[('-delete_rpath', reference)] = None
            if command not in ('LC_LOAD_DYLIB', 'LC_LOAD_WEAK_DYLIB', 'LC_REEXPORT_DYLIB', 'LC_LOAD_UPWARD_DYLIB', 'LC_ID_DYLIB'):
                continue
            if reference.startswith(SYSTEM_PREFIXES):
                continue
            if command == 'LC_ID_DYLIB':
                if reference.startswith('/'):
                    changes[('-id', '@rpath/' + path.name)] = None
                continue
            if reference.startswith('@'):
                origin = origins.get(path)
                if origin is None:
                    continue  # Bundled wheel-relative libraries keep their layout.
                prefix, relative = reference.split('/', 1)
                if prefix not in ('@rpath', '@loader_path'):
                    raise ValueError(f'Unsupported native reference in {origin}: {reference}')
                source = (origin.parent / relative).resolve(strict=True)
            else:
                if not reference.startswith(BREW_PREFIX):
                    raise ValueError(f'Unrecognized native library reference in {path}: {reference}')
                source = Path(reference).resolve(strict=True)
            target = copied.get(source)
            if target is None:
                target = libraries / source.name
                if target.exists():
                    raise ValueError(f'Native library filename collision: {target.name}')
                shutil.copyfile(source, target)
                copied[source] = target
                origins[target] = source
                pending.append(target)
            loader_relative = os.path.relpath(target, path.parent)
            replacement = '@loader_path/' + loader_relative
            if reference != replacement:
                changes[('-change', reference, replacement)] = None
        if changes:
            path.chmod(path.stat().st_mode | 0o200)
            run('/usr/bin/install_name_tool', *(item for change in changes for item in change), str(path))
            run('/usr/bin/codesign', '--force', '--sign', '-', str(path))
    return len(pending), len(copied)


def build(output, python_home, ffmpeg):
    output.mkdir()
    runtime = output / 'runtime'
    version = '3.13'
    ignore = shutil.ignore_patterns('__pycache__', 'site-packages')
    for name in ('bin', 'lib'):
        (runtime / name).mkdir(parents=True, exist_ok=True)
    # The framework's bin/python is a shim that spawns this executable.
    shutil.copy2(python_home / 'Resources/Python.app/Contents/MacOS/Python', runtime / 'bin/python')
    shutil.copytree(python_home / f'lib/python{version}', runtime / f'lib/python{version}', ignore=ignore)
    for mode, venv in (('backend', '.venv'), ('qwen', '.venv-qwen')):
        shutil.copytree(ROOT / venv / f'lib/python{version}/site-packages',
                        output / 'packages' / mode, ignore=ignore)
    shutil.copy2(ffmpeg.resolve(strict=True), runtime / 'bin/ffmpeg')
    shutil.copytree(ROOT / 'app', output / 'code/app',
                    ignore=shutil.ignore_patterns('__pycache__', 'static'))
    (output / 'code/app/static').mkdir()
    shutil.copy2(ROOT / 'app/static/index.html', output / 'code/app/static/index.html')
    (output / 'code/scripts').mkdir()
    for source in (ROOT / 'scripts').glob('*.py'):
        shutil.copy2(source, output / 'code/scripts' / source.name)
    launcher = output / 'python'
    launcher.write_text('''#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(/usr/bin/dirname -- "$0")" && pwd)
mode=$1
shift
case "$mode" in backend|qwen) ;; *) echo "Expected backend or qwen" >&2; exit 2 ;; esac
unset __PYVENV_LAUNCHER__
export PYTHONHOME="$root/runtime"
export PYTHONPATH="$root/packages/$mode:$root/code"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export PATH="$root/runtime/bin:/usr/bin:/bin"
exec "$root/runtime/bin/python" "$@"
''')
    launcher.chmod(0o755)
    images, libraries = relocate(output)
    print(json.dumps({'native_images': images, 'copied_libraries': libraries, 'bundle': str(output)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--python-home', required=True, type=Path)
    parser.add_argument('--ffmpeg', required=True, type=Path)
    args = parser.parse_args()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        parser.error('D01 requires an Apple Silicon Mac')
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error('Use an output directory outside the repository')
    build(output, args.python_home, args.ffmpeg)


if __name__ == '__main__':
    main()
