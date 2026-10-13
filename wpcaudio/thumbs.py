"""A loudness silhouette for a block, cheap enough to do for all of them.

The editor draws two hundred blocks at once and wants each one to show
what it holds. Decoding them to do that is not an option: the CVSD
decoder is a per-bit Python loop, and the three chips hold a megabyte
of it. So the silhouette comes straight off the bytes.

- **DAC** is trivially a waveform: the samples *are* the bytes, centred
  on 0x80, so the envelope is `max |byte - 0x80|` per bucket.
- **CVSD** has no samples to measure, but it does not need them. The
  chip's step size is driven by its syllabic filter, which charges on
  *coincidence* -- three identical bits in a row -- and decays
  otherwise. Loud passages are the ones that make the signal move
  faster than the current step can follow, so they are dense in
  coincidences. Counting them per bucket tracks the envelope closely
  without decoding anything.

Values come back as small integers so a couple of hundred of them
serialise to something a browser can take in one response.
"""

import numpy as np

BUCKETS = 48
LEVELS = 15

DAC_CENTRE = 128
COINCIDENCE_BITS = 3


def _buckets(values, buckets, reduce):
    """Splits `values` into `buckets` groups and reduces each one."""
    if values.size == 0:
        return np.zeros(buckets)
    # Trim rather than pad: a partial last bucket would read as a dip
    # that is not in the audio.
    per = max(1, values.size // buckets)
    usable = per * buckets
    if usable > values.size:
        return np.full(buckets, float(reduce(values)))
    return reduce(values[:usable].reshape(buckets, per), axis=1)


def _scale(levels):
    """Normalises to 0..LEVELS, keeping quiet passages visible."""
    peak = float(levels.max()) if levels.size else 0.0
    if peak <= 0:
        return [0] * len(levels)
    # A square root keeps a quiet tail from flattening to nothing, which
    # at twenty pixels tall is the difference between "silence" and
    # "something is there".
    shaped = np.sqrt(levels / peak)
    return [int(round(value * LEVELS)) for value in shaped]


def envelope(kind, data, buckets=BUCKETS):
    """A 0..15 loudness silhouette of one block, `buckets` long."""
    raw = np.frombuffer(bytes(data), dtype=np.uint8)
    if raw.size == 0:
        return [0] * buckets

    if kind == "dac":
        swing = np.abs(raw.astype(np.int16) - DAC_CENTRE)
        return _scale(_buckets(swing.astype(np.float64), buckets, np.max))

    bits = np.unpackbits(raw)
    if bits.size < COINCIDENCE_BITS:
        return [0] * buckets
    # A coincidence is three bits the same; the windows overlap, as the
    # chip's own shift register does.
    windows = np.lib.stride_tricks.sliding_window_view(bits, COINCIDENCE_BITS)
    totals = windows.sum(axis=1)
    coincident = ((totals == 0) | (totals == COINCIDENCE_BITS)).astype(np.float64)
    return _scale(_buckets(coincident, buckets, np.mean))
