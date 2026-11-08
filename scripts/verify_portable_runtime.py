#!/usr/bin/env python3
"""Exercise an already relocated D01 probe with a synthetic speech fixture."""
import argparse
import json
import re
import subprocess
import tempfile
import time
import urllib.request
import wave
from pathlib import Path


def check_loaders(log, bundle):
    images = re.findall(r'^dyld\[\d+\]: <[^>]+> (/.+)$', log.read_text(), re.MULTILINE)
    if not images or not any(Path(image).is_relative_to(bundle) for image in images):
        raise RuntimeError(f'No bundle loader evidence in {log}')
    for image in images:
        if not (Path(image).is_relative_to(bundle) or image.startswith(('/usr/lib/', '/System/Library/'))):
            raise RuntimeError(f'Unexpected external native image: {image}')
    return len(set(images))


def verify(bundle, work, audio, model, aligner, expected):
    home = work / 'home'
    home.mkdir()
    runtime = bundle / 'runtime'
    env = {
        'HOME': str(home), 'PATH': f'{runtime}/bin:/usr/bin:/bin',
        'PYTHONHOME': str(runtime), 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'MEETING_DATA': str(work / 'data'), 'MEETING_BACKUPS': str(work / 'backups'),
        'DYLD_PRINT_LIBRARIES': '1',
    }
    python = runtime / 'bin/python'
    ffmpeg = runtime / 'bin/ffmpeg'
    wav = work / 'speech.wav'

    def run(log, *command, mode='backend'):
        with (work / log).open('w') as errors:
            return subprocess.check_output(
                command, text=True, cwd=work, stderr=errors, timeout=120,
                env={**env, 'PYTHONPATH': f'{bundle}/packages/{mode}:{bundle}/code'},
            )

    run('ffmpeg.log', ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
        '-i', audio, '-ac', '1', '-ar', '16000', wav)
    with wave.open(str(wav), 'rb') as stream:
        if stream.getnchannels() != 1 or stream.getframerate() != 16000 or not stream.getnframes():
            raise RuntimeError('FFmpeg did not produce usable mono 16 kHz PCM')
        duration = stream.getnframes() / stream.getframerate()

    backend_log = work / 'backend.log'
    with backend_log.open('w') as logs:
        backend = subprocess.Popen(
            [python, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '0'],
            cwd=work, stdout=logs, stderr=logs,
            env={**env, 'PYTHONPATH': f'{bundle}/packages/backend:{bundle}/code'},
        )
        try:
            deadline = time.monotonic() + 25
            while not (port := re.search(r'Uvicorn running on http://127\.0\.0\.1:(\d+)', backend_log.read_text())):
                if backend.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f'Bundled backend did not start: {backend_log}')
                time.sleep(.1)
            with urllib.request.urlopen(f'http://127.0.0.1:{port[1]}/api/runtime/host', timeout=5) as response:
                if json.load(response)['pid'] != backend.pid:
                    raise RuntimeError('The health response came from a different backend')
        finally:
            backend.terminate()
            try:
                backend.wait(timeout=5)
            except subprocess.TimeoutExpired:
                backend.kill()
                backend.wait()

    output = work / 'asr.json'
    script = bundle / 'code/scripts/qwen_transcribe.py'
    bootstrap = (
        'import runpy,sys; import mlx.core as mx; '
        'mx.set_default_device(mx.gpu); '
        'print(mx.device_info()["device_name"], flush=True); '
        'sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name="__main__")'
    )
    stdout = run('mlx.log', python, '-c', bootstrap, script, '--draft', '--language', 'en',
                 '--audio', wav, '--model', model, '--aligner', aligner, '--output', output, mode='qwen')
    asr = json.loads(output.read_text())
    words = lambda value: re.findall(r'[a-z]+', value.lower())
    if not asr['segments'] or words(asr['text']) != words(expected):
        raise RuntimeError('MLX transcription differs from the expected synthetic phrase')
    return {
        'audio_seconds': round(duration, 3), 'backend': 'HTTP verified',
        'mlx': stdout.splitlines()[0], 'transcript': asr['text'],
        'inference_seconds': round(asr['elapsed_seconds'], 3),
        'loaded_images': {name: check_loaders(work / f'{name}.log', bundle)
                          for name in ('ffmpeg', 'backend', 'mlx')},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'audio', 'model', 'aligner'):
        parser.add_argument(f'--{name}', required=True, type=Path)
    parser.add_argument('--expect', required=True)
    args = parser.parse_args()
    paths = [path.resolve(strict=True) for path in (args.bundle, args.audio, args.model, args.aligner)]
    work = Path(tempfile.mkdtemp(prefix='tingji-runtime-smoke-')).resolve()
    print(f'Evidence directory: {work}', flush=True)
    print(json.dumps(verify(paths[0], work, *paths[1:], args.expect), ensure_ascii=False))


if __name__ == '__main__':
    main()
