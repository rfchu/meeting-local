#!/usr/bin/env python3
"""Build an isolated macOS sample, or sign and notarize an approved release."""
import argparse
import hashlib
from importlib.metadata import distributions
import json
import os
import plistlib
import re
import shutil
import shlex
import subprocess
import tempfile
from pathlib import Path

from build_portable_runtime import ROOT, native_files


def run(*args, **kwargs):
    return subprocess.check_output([str(arg) for arg in args], cwd=ROOT, **kwargs)


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def runtime_fingerprint(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f'Unsupported runtime entry: {path.relative_to(root)}')
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            digest.update(json.dumps([relative, sha256(path), path.stat().st_mode & 0o777]).encode())
            digest.update(b'\n')
    return digest.hexdigest()


def identity_available(identity):
    identities = run('/usr/bin/security', 'find-identity', '-v', '-p', 'codesigning', text=True)
    for line in identities.splitlines():
        match = re.fullmatch(r'\s*\d+\) ([0-9A-F]{40}) "(Developer ID Application: .+)"\s*', line)
        if match and identity in match.groups():
            return True
    return False


def sign(path, identity, *, executable=True, entitlements=None):
    flags = ['--timestamp'] if identity != '-' else []
    if executable and identity != '-':
        flags += ['--options', 'runtime']
    if entitlements:
        flags += ['--entitlements', entitlements]
    run('/usr/bin/codesign', '--force', '--sign', identity, *flags, path)


def verify(path):
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', path)


def notarize(artifact, profile, receipt):
    result = subprocess.run(['/usr/bin/xcrun', 'notarytool', 'submit', str(artifact),
                             '--keychain-profile', profile, '--wait', '--timeout', '30m',
                             '--output-format', 'json'], capture_output=True, check=False)
    receipt.write_bytes(result.stdout)
    try:
        response = json.loads(result.stdout)
    except ValueError:
        response = {}
    if result.returncode or response.get('status') != 'Accepted':
        raise RuntimeError(f'Notarization did not complete. See {receipt.name} and notarytool log.')
    return response['id']


def build(args):
    runtime, output = args.runtime, args.output
    if not (runtime / 'python').is_file() or not (runtime / 'capture/TingjiCapture.app').is_dir():
        raise ValueError('Expected a portable runtime produced by the runtime builder')
    if output.is_relative_to(ROOT) or output.is_relative_to(runtime):
        raise ValueError('Use a new output directory outside the repository and runtime')
    if output.exists():
        raise ValueError('Output directory already exists')
    release = bool(args.identity)
    if args.internal_revision < 1 or (args.internal_revision != 1 and not args.internal):
        raise ValueError('Internal revision requires --internal and a positive number')
    package_version = f'0.4.0-internal.{args.internal_revision}'
    if args.internal and release:
        raise ValueError('Internal and Developer ID modes are separate')
    if release and not identity_available(args.identity):
        raise ValueError('No matching Developer ID Application identity in the keychain')
    if release and (not args.notary_profile or not args.approved_runtime_sha256 or not args.notices
                    or not args.notices.is_file() or args.notices.stat().st_size == 0):
        raise ValueError('Release requires a notary profile, reviewed runtime SHA-256, and notices file')
    if not release and (args.notary_profile or args.approved_runtime_sha256 or args.notices):
        raise ValueError('Formal release options require a Developer ID Application identity')
    fingerprint = runtime_fingerprint(runtime)
    if release and fingerprint != args.approved_runtime_sha256.lower():
        raise ValueError('Runtime differs from the reviewed distribution input')
    ffmpeg = run(runtime / 'runtime/bin/ffmpeg', '-version', text=True)
    configure = next((line.partition(': ')[2] for line in ffmpeg.splitlines()
                      if line.startswith('configuration:')), '')
    flags = shlex.split(configure)
    if release and (not configure or {'--enable-gpl', '--enable-nonfree'} & set(flags)):
        raise ValueError('This release pipeline requires an approved LGPL FFmpeg build')
    output.mkdir(mode=0o700)
    identity = args.identity or '-'
    with tempfile.TemporaryDirectory(prefix='.build-', dir=output) as temp:
        work = Path(temp)
        static = work / 'web'
        subprocess.run(['npm', 'run', 'build', '--', '--outDir', str(static)], cwd=ROOT/'frontend', check=True)
        helper = work / '.tingji-capture-releases/release/TingjiCapture.app'
        app = work / '.tingji-host-releases/release/Tingji.app'
        # Limit configuration inputs to the isolated build and a fixed default port.
        env = os.environ.copy()
        for name in ('PORT', 'MEETING_DATA', 'MEETING_BACKUPS', 'TINGJI_HOST_STATE_DIR', 'TINGJI_INTERNAL_BUILD'):
            env.pop(name, None)
        if args.internal:
            env.update(PORT='18876', TINGJI_INTERNAL_BUILD=str(5 + args.internal_revision))
        run('/bin/bash', 'scripts/compile_capture.sh', env=env | {
            'TINGJI_CAPTURE_STANDALONE_BUILD': '1', 'TINGJI_CAPTURE_COMPILE_APP': str(helper)})
        run('/bin/bash', 'scripts/compile_host.sh', env=env | {
            'TINGJI_HOST_COMPILE_APP': str(app), 'TINGJI_PORTABLE_RUNTIME_SOURCE': str(runtime),
            'TINGJI_PORTABLE_STATIC': str(static)})
        embedded = app / 'Contents/Resources/PortableRuntime'
        shutil.rmtree(embedded / 'capture')
        (embedded / 'capture').mkdir()
        bundled_helper = embedded / 'capture/TingjiCapture.app'
        helper.rename(bundled_helper)
        if args.notices:
            shutil.copyfile(args.notices, app / 'Contents/Resources/ThirdPartyNotices.txt')
        images = list(native_files(embedded))
        for image in images:
            if not image.is_relative_to(bundled_helper):
                sign(image, identity)
        audio_permissions = work / 'audio-input.plist'
        audio_permissions.write_bytes(plistlib.dumps({'com.apple.security.device.audio-input': True}))
        sign(bundled_helper, identity, entitlements=audio_permissions)
        sign(app, identity, entitlements=audio_permissions)
        verify(app)
        verify(bundled_helper)
        with (app / 'Contents/Info.plist').open('rb') as stream:
            info = plistlib.load(stream)
        with (bundled_helper / 'Contents/Info.plist').open('rb') as stream:
            helper_id = plistlib.load(stream)['CFBundleIdentifier']
        evidence = {'version': info['CFBundleShortVersionString'], 'build': info['CFBundleVersion'],
                    'app_id': info['CFBundleIdentifier'], 'capture_id': helper_id,
                    'minimum_macos': info['LSMinimumSystemVersion'], 'runtime_input_sha256': fingerprint,
                    'ffmpeg': ffmpeg.splitlines()[:3], 'native_images': len(images) + 1,
                    'signing': 'developer-id' if release else 'ad-hoc', 'notarized': False}
        if args.internal:
            evidence.update(channel='internal', package_version=package_version, port=18876)
        evidence['source_revision'] = run('/usr/bin/git', 'rev-parse', 'HEAD', text=True).strip()
        evidence['source_has_local_changes'] = bool(run('/usr/bin/git', 'status', '--porcelain'))
        evidence['packages'] = {
            mode: sorted([dist.metadata['Name'], dist.version] for dist in
                         distributions(path=[str(embedded / 'packages' / mode)]) if dist.metadata['Name'])
            for mode in ('backend', 'qwen')
        }
        if args.notices:
            evidence['notices_sha256'] = sha256(args.notices)
        if release:
            archive = work / 'Tingji.zip'
            run('/usr/bin/ditto', '-c', '-k', '--keepParent', app, archive)
            evidence['app_submission'] = notarize(archive, args.notary_profile, output / 'app-notary.json')
            run('/usr/bin/xcrun', 'stapler', 'staple', app)
            run('/usr/bin/xcrun', 'stapler', 'validate', app)
            run('/usr/sbin/spctl', '--assess', '--type', 'execute', app)
        contents = work / 'image'
        contents.mkdir()
        app.rename(contents / app.name)
        (contents / 'Applications').symlink_to('/Applications')
        dmg_name = 'Tingji.dmg' if release else 'Tingji-local-sample.dmg'
        if args.internal:
            dmg_name = f'Tingji-{package_version}.dmg'
        dmg = work / dmg_name
        run('/usr/bin/hdiutil', 'create', '-volname', 'Tingji', '-srcfolder', contents, '-format', 'UDZO', dmg)
        if release:
            sign(dmg, identity, executable=False)
            evidence['dmg_submission'] = notarize(dmg, args.notary_profile, output / 'dmg-notary.json')
            run('/usr/bin/xcrun', 'stapler', 'staple', dmg)
            run('/usr/bin/xcrun', 'stapler', 'validate', dmg)
            run('/usr/sbin/spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature', dmg)
            evidence['notarized'] = True
        run('/usr/bin/hdiutil', 'verify', dmg)
        evidence['dmg_sha256'] = sha256(dmg)
        dmg.rename(output / dmg.name)
        (contents / app.name).rename(output / app.name)
        (output / 'release.json').write_text(json.dumps(evidence, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=lambda value: Path(value).resolve(strict=True))
    parser.add_argument('output', type=lambda value: Path(value).resolve())
    parser.add_argument('--internal', action='store_true', help='Build an internal standalone package')
    parser.add_argument('--internal-revision', type=int, default=1)
    parser.add_argument('--identity', help='Developer ID Application name or certificate SHA-1')
    parser.add_argument('--notary-profile', help='Existing notarytool keychain profile')
    parser.add_argument('--approved-runtime-sha256', help='Fingerprint of the runtime reviewed for redistribution')
    parser.add_argument('--notices', type=lambda value: Path(value).resolve(strict=True))
    args = parser.parse_args()
    try:
        build(args)
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
