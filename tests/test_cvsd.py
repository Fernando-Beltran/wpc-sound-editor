import numpy as np

from wpcaudio.cvsd import Hc55516Decoder


def test_decode_returns_one_int16_sample_per_bit():
    out = Hc55516Decoder().decode([0, 1] * 50)
    assert out.dtype == np.int16
    assert len(out) == 100


def test_run_of_ones_drives_output_positive():
    out = Hc55516Decoder().decode([1] * 64)
    assert out[-1] > 0


def test_run_of_zeros_drives_output_negative():
    out = Hc55516Decoder().decode([0] * 64)
    assert out[-1] < 0


def test_unpack_bits_takes_most_significant_bit_first():
    from wpcaudio.cvsd import unpack_bits

    assert list(unpack_bits(b"\x80")) == [1, 0, 0, 0, 0, 0, 0, 0]


def test_unpack_bits_can_take_least_significant_bit_first():
    from wpcaudio.cvsd import unpack_bits

    assert list(unpack_bits(b"\x01", msb_first=False)) == [1, 0, 0, 0, 0, 0, 0, 0]


def test_unpack_bits_yields_eight_bits_per_byte():
    from wpcaudio.cvsd import unpack_bits

    assert len(unpack_bits(b"\x00\xff\x5a")) == 24


def test_an_idle_alternating_stream_stays_near_silence():
    # A CVSD stream at rest alternates up and down: no coincidence, so
    # the syllabic filter never charges and the step stays at nothing.
    # This is why a short replacement sample is padded with 0x55.
    out = Hc55516Decoder().decode([0, 1] * 2000)

    assert np.abs(out).max() < 2000, "el reposo no es silencio: pico %d" % np.abs(out).max()


def test_arbitrary_data_does_not_drive_the_output_to_the_rails():
    # The failure this guards against: an integrator that leaks too
    # slowly accumulates DC and pins the output at full scale, which
    # made every extracted voice block come out clipped and hot.
    rng = np.random.default_rng(0)
    out = Hc55516Decoder().decode(rng.integers(0, 2, 40000)).astype(float)

    saturated = np.mean(np.abs(out) > 32000)
    assert saturated < 0.01, "%.1f%% de las muestras saturan" % (100 * saturated)
    assert np.sqrt((out ** 2).mean()) < 8000, "nivel medio excesivo"


def test_the_integrator_leaks_back_toward_zero():
    # A long run of ones pushes the output up; once the bits alternate
    # the integrator has to fall back, with the ~1 ms time constant the
    # data sheet quotes for the signal estimator.
    decoder = Hc55516Decoder()
    decoder.decode([1] * 200)
    peak = abs(int(decoder.output))
    decoder.decode([0, 1] * 200)

    assert abs(int(decoder.output)) < peak / 4
