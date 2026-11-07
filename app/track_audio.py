"""Reference-based echo reduction for recognition; never modifies source audio."""
import io
import unicodedata
import wave

import numpy as np

RATE = 16000
ECHO_VERSION = 2


def _reduce_echo(system, microphone, fitted_blocks=None):
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
        if fitted_blocks is not None:
            fitted_blocks.append((start, end))
        result[start:end] = np.rint(np.clip(mic - echo, -32768, 32767)).astype(np.int16)
    return result


def wav_samples(source):
    with wave.open(source, 'rb') as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, RATE):
            raise ValueError('音轨须为 16 kHz 单声道 PCM')
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')


def peak_frame_rms(samples):
    """Measure 20 ms frames so a quiet window cannot hide a short utterance."""
    if not len(samples):
        return 0
    frames = np.pad(samples.astype(np.float64), (0, -len(samples) % 320)).reshape(-1, 320)
    return np.sqrt(np.max(np.mean(frames * frames, axis=1)))


def reduce_echo_wav(system, microphone):
    mic = wav_samples(io.BytesIO(microphone))
    cleaned = _reduce_echo(wav_samples(io.BytesIO(system)), mic)
    if peak_frame_rms(cleaned) < 64:
        cleaned = np.zeros_like(cleaned)
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        wav.writeframes(cleaned.astype('<i2', copy=False).tobytes())
    return output.getvalue()


def echo_dominated(system, mic):
    fitted = []
    residual = _reduce_echo(system, mic, fitted)
    energy = np.square(mic.astype(np.float64))
    total = energy.sum()
    return bool(
        total
        and sum(energy[start:end].sum() for start, end in fitted) >= 0.85 * total
        and peak_frame_rms(residual) < 450
    )


def remove_echo_duplicates(segments, corroborate):
    """Remove a mic row only when text, time and the original audio all agree."""
    def normalized(text):
        return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())

    system_by_text = {}
    for segment in segments:
        if segment.get('source_track') == 'system':
            system_by_text.setdefault(normalized(segment['text']), []).append(segment)
    kept = []
    for segment in segments:
        if segment.get('source_track') == 'microphone' and not (segment.get('live_correction_id') or segment.get('speaker_locked')):
            text = normalized(segment['text'])
            start, end = segment['start'], segment['end']
            for online in system_by_text.get(text, ()) if len(text) >= 4 and end > start else ():
                overlap = min(end, online['end']) - max(start, online['start'])
                if overlap >= 0.8 * (end - start) and corroborate(start, end):
                    break
            else:
                kept.append(segment)
            continue
        kept.append(segment)
    return kept


def wav_echo_dominated(system, mic, start, end):
    def read(source):
        with wave.open(source, 'rb') as wav:
            first = min(wav.getnframes(), max(0, round(start * RATE)))
            wav.setpos(first)
            return np.frombuffer(wav.readframes(max(0, round(end * RATE) - first)), dtype='<i2')
    return echo_dominated(read(system), read(mic))
