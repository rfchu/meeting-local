import numpy as np

from app.track_audio import RATE, _reduce_echo


def test_correlated_echo_is_removed_without_dropping_overlapping_local_voice():
    rng = np.random.default_rng(82)
    system = rng.integers(-6500, 6500, size=2*RATE, dtype=np.int16)
    local = rng.integers(-1800, 1800, size=len(system), dtype=np.int16)
    echo = np.r_[np.zeros(419), system[:-419]*0.78].astype(np.int16)
    mixed = local + echo
    cleaned = _reduce_echo(system, mixed)
    assert np.std(cleaned.astype(float) - local) < np.std(echo)*0.14
    assert np.corrcoef(cleaned, local)[0, 1] > 0.96
    assert np.array_equal(_reduce_echo(system, local), local)
    assert np.array_equal(mixed, local + echo)
