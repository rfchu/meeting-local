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


def test_duplicate_requires_timing_and_acoustic_evidence():
    from app.track_audio import echo_dominated, remove_echo_duplicates

    rng = np.random.default_rng(42)
    system = rng.integers(-5000, 5000, size=RATE * 2, dtype=np.int16)
    echo = np.r_[np.zeros(417), system[:-417] * 0.08].astype(np.int16)
    voice = rng.integers(-1300, 1300, size=len(system), dtype=np.int16)
    assert echo_dominated(system, echo)
    assert not echo_dominated(system, echo + voice)

    online = {'start': 0, 'end': 2, 'text': '周五交付蓝色版本。', 'source_track': 'system'}
    mic = dict(online, text='周五交付蓝色版本', source_track='microphone')
    separate = dict(mic, start=3, end=5)
    edited = dict(mic, live_correction_id='reviewed')
    independent = dict(mic, text='周四检查红色名单')
    segments = [online, mic, separate, edited, independent]
    assert remove_echo_duplicates(segments, lambda *_: echo_dominated(system, echo)) == [
        online, separate, edited, independent,
    ]
    assert remove_echo_duplicates(segments, lambda *_: echo_dominated(system, echo + voice)) == segments


def test_near_duplicate_preserves_interjection_and_uncertain_differences():
    from app.track_audio import limit_asr_repetition, remove_echo_duplicates

    online = {'start': 0, 'end': 8, 'source_track': 'system',
              'text': '并把我们拿到的数据第一时间分享给各位。挑三拣四，昨天。'}
    mic = dict(online, source_track='microphone',
               text='并把我们拿到的数据第一时间分享给各位。测的都不对，都不对。挑三拣四。昨天。')
    assert remove_echo_duplicates([online, mic], lambda *_:'mixed', mark_mixed=True) == [
        online, dict(mic, text='测的都不对，都不对。', echo_original_text=mic['text'], possible_echo=False),
    ]
    uncertain = dict(mic, text=mic['text'].replace('挑三拣四。昨天。', '今天。'))
    assert remove_echo_duplicates([online, uncertain], lambda *_:'mixed', mark_mixed=True) == [
        online, dict(uncertain, possible_echo=True),
    ]
    assert remove_echo_duplicates([online, mic], lambda *_:'none', mark_mixed=True) == [online, mic]
    loop = '错的都不对，' + '都不对，' * 30 + '我说明天'
    assert limit_asr_repetition(loop) == '错的都不对，都不对，我说明天'


def test_sustained_echo_drops_identical_text_but_keeps_anchored_local_question():
    from app.track_audio import remove_echo_duplicates

    online = {'start': 8, 'end': 16, 'source_track': 'system',
              'text': '截至九月，全球提供英伟达GPU算力的公司总共有三百二十三家。对这些公司的前景，咨询机构Gartner预。'}
    duplicate = dict(online, source_track='microphone')
    question = dict(duplicate, text=duplicate['text'].replace('。对这些', '。这是啥情况呢？对这些'))
    evidence = lambda *_: 'sustained'
    assert remove_echo_duplicates([online, duplicate], evidence) == [online]
    kept = remove_echo_duplicates([online, question], evidence)
    assert kept[1]['text'] == '这是啥情况呢？'
    assert kept[1]['echo_original_text'] == question['text']
    assert remove_echo_duplicates(kept, evidence) == kept
    assert remove_echo_duplicates([online, question], lambda *_:'none') == [online, question]
