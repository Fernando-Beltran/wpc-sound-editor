import numpy as np

from wpcaudio.cvsd import Hc55516Decoder, Hc55516Encoder

BIT_RATE = 22050


def _tone(freq, seconds=0.5, amplitude=12000, rate=BIT_RATE):
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def test_encoder_emits_one_bit_per_sample():
    bits = Hc55516Encoder().encode(np.zeros(100, dtype=np.int16))
    assert len(bits) == 100


def test_encoder_emits_only_zeros_and_ones():
    bits = Hc55516Encoder().encode(_tone(300))
    assert set(np.unique(bits)).issubset({0, 1})


def test_round_trip_reproduces_a_voice_band_tone():
    original = _tone(300)
    back = Hc55516Decoder().decode(Hc55516Encoder().encode(original))

    correlation = np.corrcoef(original.astype(float), back.astype(float))[0, 1]
    assert correlation > 0.9, "reconstruccion pobre: r=%.3f" % correlation


def test_round_trip_tracks_a_quiet_signal_without_blasting():
    quiet = _tone(300, amplitude=1500)
    back = Hc55516Decoder().decode(Hc55516Encoder().encode(quiet))

    loud = np.sqrt((back.astype(float) ** 2).mean())
    assert loud < 4 * np.sqrt((quiet.astype(float) ** 2).mean()), (
        "la salida se dispara: rms %.0f frente a %.0f" % (loud, np.sqrt((quiet.astype(float) ** 2).mean()))
    )


def test_reconstruction_filter_removes_the_out_of_band_hiss():
    # CVSD leaves granular quantisation noise across the whole band. The
    # board's analog low-pass can only remove what sits above the voice
    # band; in-band noise measures about -27 dB here and is irreducible
    # by filtering, so it caps total SNR near 27 dB at this bit rate. Do
    # not chase a higher figure with a filter -- raise the bit rate.
    from wpcaudio.filters import lowpass

    original = _tone(300, seconds=1.0)
    decoded = Hc55516Decoder().decode(Hc55516Encoder().encode(original))
    filtered = lowpass(decoded, BIT_RATE, cutoff=3400)

    def out_of_band_db(signal):
        spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal)))) ** 2
        freqs = np.fft.rfftfreq(len(signal), 1 / BIT_RATE)
        fundamental = spectrum[(freqs >= 250) & (freqs < 350)].sum()
        return 10 * np.log10(spectrum[freqs >= 4000].sum() / fundamental)

    before, after = out_of_band_db(decoded), out_of_band_db(filtered)

    assert after < -38, "ruido fuera de banda solo %.1f dB bajo el tono" % -after
    assert before - after > 15, "el filtro solo gana %.1f dB" % (before - after)
