"""Telling apart the kinds of data that share a WPC sound ROM.

A sound ROM mixes 8-bit DAC samples, CVSD voice, 6809 code and padding
in one address space with nothing marking the boundaries. Byte
statistics separate them without any disassembly:

- DAC samples are a sampled waveform, so adjacent bytes are strongly
  correlated and the values sit around a centre.
- A CVSD bitstream carries one bit per sample, so consecutive bytes are
  nearly independent: high entropy, correlation close to zero.
- Padding is a single repeated byte: no entropy at all.
"""

from collections import namedtuple

import numpy as np

BlockStats = namedtuple("BlockStats", "offset size entropy autocorr fill_ratio mean")


def block_stats(data, offset=0):
    """Summarises one block of ROM bytes."""
    raw = np.frombuffer(data, dtype=np.uint8)
    counts = np.bincount(raw, minlength=256)

    present = counts[counts > 0]
    probabilities = present / raw.size
    entropy = float(-np.sum(probabilities * np.log2(probabilities)))

    values = raw.astype(np.float64)
    # Correlate the block against itself shifted by one byte. Either
    # half can be constant on its own, so guard both: Pearson's formula
    # divides by each standard deviation and would yield nan, which
    # silently loses every later comparison.
    leading, lagging = values[:-1], values[1:]
    if leading.size == 0 or leading.std() == 0.0 or lagging.std() == 0.0:
        autocorr = 0.0
    else:
        autocorr = float(np.corrcoef(leading, lagging)[0, 1])

    return BlockStats(
        offset=offset,
        size=raw.size,
        entropy=entropy,
        autocorr=autocorr,
        fill_ratio=float(counts.max() / raw.size),
        mean=float(values.mean()),
    )


def scan(data, block_size=1024):
    """Walks a ROM image block by block, yielding statistics for each."""
    return [
        block_stats(data[start:start + block_size], offset=start)
        for start in range(0, len(data) - block_size + 1, block_size)
    ]
