"""Reading and writing plain mono 16-bit WAV.

Every entry point takes either a path or an open binary file, so the
same code serves a command-line script writing to disk and an editor
handing a browser the bytes of a block without a temporary file.
"""

import wave

import numpy as np


def _target(path_or_file):
    """`wave` takes a path or a file object; normalise to one of them."""
    return path_or_file if hasattr(path_or_file, "write") else str(path_or_file)


def _source(path_or_file):
    return path_or_file if hasattr(path_or_file, "read") else str(path_or_file)


def write_wav(path, samples, sample_rate):
    """Writes int16 samples to `path` as mono 16-bit PCM at `sample_rate`."""
    frames = np.asarray(samples, dtype=np.int16).astype("<i2", copy=False)
    with wave.open(_target(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(int(sample_rate))
        out.writeframes(frames.tobytes())
    return path


def read_wav(path):
    """Reads a mono or stereo PCM WAV, returning (int16 samples, rate).

    Stereo is mixed down: a replacement sample feeds one mono channel
    on the board, so carrying two around would only invite confusion.
    """
    with wave.open(_source(path), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("solo PCM de 16 bits; este tiene %d bytes por muestra"
                             % source.getsampwidth())
        channels = source.getnchannels()
        rate = source.getframerate()
        raw = source.readframes(source.getnframes())

    samples = np.frombuffer(raw, dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return np.array(samples, dtype=np.int16), rate
