#!/usr/bin/env python3
"""Verify the signed D02 helper through the relocated backend's native probe."""
import argparse
import json
import os
import subprocess
import tempfile
import time
import wave
from array import array
from pathlib import Path

PROBE_ID = 'cn.seeknow.tingji.capture.probe'


def verify(bundle, source):
    helper_app = bundle / 'capture/TingjiCapture.app'
    helper = helper_app / 'Contents/MacOS/TingjiCapture'
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict',
                    '-R', f'=identifier "{PROBE_ID}"', helper_app], check=True, timeout=10)
    evidence = Path(tempfile.mkdtemp(prefix='tingji-capture-smoke-')).resolve()
    os.environ['TINGJI_CAPTURE_APP'] = str(helper_app)
    os.environ['TINGJI_CAPTURE_DELIVERY_ROOT'] = str(evidence / 'binary-locks')
    os.environ['TINGJI_CAPTURE_CONTROL_ROOT'] = str(evidence / 'control')
    from app.capture_probe import CaptureProbe
    from app.db import Store
    from app.native_capture import NativeCapture
    from app.recordings import RecordingStore

    store = Store(evidence / 'data')
    native = NativeCapture(store, RecordingStore(store))
    capabilities = native.capability()
    self_test = json.loads(subprocess.check_output([helper, '--diagnostics-self-test'], timeout=5))
    if not capabilities['available'] or not capabilities['supported'] or not self_test:
        raise RuntimeError('Packaged capture helper did not pass its preflight')
    result = {'capabilities': capabilities, 'evidence_directory': str(evidence)}
    if not source:
        return result

    probe = CaptureProbe(native)
    item = probe.start(source)
    key = item['id']
    try:
        deadline = time.monotonic() + 50
        while (item := probe.read(key))['state'] in ('starting', 'recording'):
            if time.monotonic() > deadline:
                raise TimeoutError('Capture probe did not complete')
            time.sleep(.2)
        result['capture'] = item
        folder = probe.items[key]['folder']
        report = folder / 'capture.json'
        if report.is_file():
            result['diagnostics'] = json.loads(report.read_text()).get('diagnostics', {})
        result['tracks'] = {}
        for name in ('system', 'microphone'):
            pcm = folder / f'{name}.pcm'
            if not pcm.exists():
                continue
            data = pcm.read_bytes()
            samples = array('h', data)
            with wave.open(str(evidence / f'{name}.wav'), 'wb') as wav:
                wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                wav.writeframes(data)
            result['tracks'][name] = {'frames': len(samples), 'peak': max(map(abs, samples), default=0)}
        return result
    finally:
        probe.delete(key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--source', choices=('macos-system', 'macos-both'),
                        help='Explicitly request a real, time-limited recording')
    parser.add_argument('--expect', choices=('recorded', 'screen-denied', 'microphone-denied'),
                        default='recorded')
    args = parser.parse_args()
    if args.expect != 'recorded' and not args.source:
        parser.error('Permission-denial verification requires a capture source')
    result = verify(args.bundle.resolve(strict=True), args.source)
    print(json.dumps(result, ensure_ascii=False))
    if not args.source:
        return
    capture, tracks = result['capture'], result['tracks']
    if args.expect == 'recorded':
        expected_tracks = {'system', 'microphone'} if args.source == 'macos-both' else {'system'}
        if (capture['state'] != 'completed' or set(tracks) != expected_tracks
                or any(not track['frames'] or not track['peak'] for track in tracks.values())):
            raise SystemExit('Expected non-silent audio on all requested tracks')
    else:
        reason = {'screen-denied': '屏幕与系统音频录制', 'microphone-denied': '麦克风未授权'}[args.expect]
        if capture['state'] != 'failed' or tracks or reason not in capture['error']:
            raise SystemExit('Expected a permission refusal without audio files')


if __name__ == '__main__':
    main()
