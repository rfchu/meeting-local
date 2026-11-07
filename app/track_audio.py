"""Reference-based echo reduction for recognition; never modifies source audio."""
import io
import wave

import numpy as np

RATE = 16000
ECHO_VERSION = 1


def _reduce_echo(system, microphone):
    """Subtract only a strongly correlated, delayed copy of the system track."""
    length = min(len(system), len(microphone))
    if length < RATE // 2:
        return microphone
    result = microphone.copy()
    max_lag = RATE // 4
    for start in range(0, length, RATE):
        end = min(start + RATE, length)
        reference_start = max(0, start - max_lag)
        ref = system[reference_start:end].astype(np.float64)
        mic = microphone[start:end].astype(np.float64)
        if len(mic) < RATE // 4 or np.linalg.norm(ref) < 80 * np.sqrt(len(ref)):
            continue
        pad = start - reference_start
        aligned = np.pad(mic, (pad, 0))
        fft_size = 1 << (2 * len(ref) - 1).bit_length()
        cross = np.fft.irfft(np.fft.rfft(aligned, fft_size) * np.fft.rfft(ref, fft_size).conj(), fft_size)
        candidates = cross[:max_lag + 1]
        lag = int(np.argmax(np.abs(candidates)))
        if abs(candidates[lag]) < 0.36 * np.linalg.norm(ref) * np.linalg.norm(mic):
            continue
        # Learn a short impulse response on top of the direct path. A small
        # ridge term avoids unstable gains on tonal sounds.
        offsets = np.arange(max(0, lag - 32), lag + 97, 4)
        indices = np.arange(start, end)[:, None] - offsets
        design = system[np.clip(indices, 0, len(system)-1)].astype(np.float64)
        design[indices < 0] = 0
        sample = design[::4]
        gram = sample.T @ sample
        ridge = np.trace(gram) / len(offsets) * 0.025
        gains = np.linalg.solve(gram + np.eye(len(offsets)) * max(ridge, 1), sample.T @ mic[::4])
        echo = design @ gains
        if np.linalg.norm(mic - echo) > 0.96 * np.linalg.norm(mic):
            continue
        result[start:end] = np.rint(np.clip(mic - echo, -32768, 32767)).astype(np.int16)
    return result


def reduce_echo_wav(system, microphone):
    def samples(payload):
        with wave.open(io.BytesIO(payload), 'rb') as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, RATE):
                raise ValueError('音轨须为 16 kHz 单声道 PCM')
            return np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
    mic = samples(microphone)
    cleaned = _reduce_echo(samples(system), mic)
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        wav.writeframes(cleaned.astype('<i2', copy=False).tobytes())
    return output.getvalue()
