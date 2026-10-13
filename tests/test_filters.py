import numpy as np

from wpcaudio.filters import lowpass

RATE = 22050


def _tone(freq, seconds=0.5, amplitude=10000.0, rate=RATE):
    t = np.arange(int(seconds * rate)) / rate
    return amplitude * np.sin(2 * np.pi * freq * t)


def _rms(x):
    return float(np.sqrt((np.asarray(x, dtype=np.float64) ** 2).mean()))


def _settled(x):
    # Skip the filter's start-up transient.
    return np.asarray(x)[RATE // 10:]


def test_voice_band_tone_passes_almost_untouched():
    tone = _tone(300)
    out = lowpass(tone, RATE, cutoff=3400)

    assert _rms(_settled(out)) > 0.9 * _rms(_settled(tone))


def test_tone_well_above_the_cutoff_is_strongly_attenuated():
    tone = _tone(8000)
    out = lowpass(tone, RATE, cutoff=3400)

    attenuation_db = 20 * np.log10(_rms(_settled(tone)) / _rms(_settled(out)))
    assert attenuation_db > 24, "solo %.1f dB" % attenuation_db


def test_filtering_does_not_change_the_sample_count():
    assert len(lowpass(_tone(300), RATE, cutoff=3400)) == len(_tone(300))
