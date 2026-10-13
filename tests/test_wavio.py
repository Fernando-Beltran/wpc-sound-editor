import wave

import numpy as np

from wpcaudio.wavio import write_wav


def test_written_wav_is_mono_16bit_at_the_given_rate(tmp_path):
    path = tmp_path / "tone.wav"
    write_wav(path, np.array([0, 1000, -1000, 32767, -32768], dtype=np.int16), 22050)

    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 22050
        assert w.getnframes() == 5


def test_written_samples_read_back_unchanged(tmp_path):
    path = tmp_path / "tone.wav"
    samples = np.array([0, 1000, -1000, 32767, -32768], dtype=np.int16)
    write_wav(path, samples, 8000)

    with wave.open(str(path), "rb") as w:
        back = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert np.array_equal(back, samples)
