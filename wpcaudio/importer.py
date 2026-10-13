"""Turning a WAV into bytes that fit a block of the sound ROM.

The constraint that shapes all of this is that **a block cannot grow**:
its end address is in the table and the next block starts there, so a
replacement is resampled to the board's own rate, encoded for whichever
chip plays the block, and then made to fit. `prepare` reports what that
cost so the caller can tell the user before anything is written.

**How it is made to fit is the caller's choice**, through `mode`:

- `trim` keeps a window of the audio, which `offset` slides; short
  audio is placed within the block with silence around it.
- `loop` repeats short audio until the block is full, so a hum or an
  engine note fills its slot without a gap.
- `speed` resamples the whole thing to exactly the block's length,
  which costs pitch -- the right trade for a near miss, the wrong one
  for a phrase at half speed.
- `auto` picks: a mismatch within `AUTO_SPEED_TOLERANCE` is nudged by
  speed, since a few per cent of pitch is less noticeable than a cut;
  anything further off is looped if short and trimmed if long.

Either way the block keeps its size, because its end address is in the
ROM's table and the next block begins there.

The window is taken **before encoding, in the sample domain**. Slicing
an encoded CVSD stream instead would leave the decoder starting
mid-stream with the wrong integrator and step, and padding it with
silence bytes would splice an unrelated bit pattern onto a live signal.
Cutting the samples and encoding the result avoids both.

Resampling is linear interpolation over a low-pass, which keeps numpy
as the project's only dependency. Downsampling filters first, at a
little under the new Nyquist limit, because folding the top of a 44 kHz
WAV back into the voice band is exactly the kind of damage that would
later be blamed on the codec.
"""

from collections import namedtuple

import numpy as np

from wpcaudio.cvsd import Hc55516Encoder, pack_bits
from wpcaudio.dac import encode_dac
from wpcaudio.filters import lowpass, to_int16
from wpcaudio.soundrom import fit_payload

TrimReport = namedtuple(
    "TrimReport",
    "size capacity_samples source_samples offset max_offset truncated padded "
    "capacity_seconds source_seconds offset_seconds rate mode speed",
)

MODES = ("trim", "loop", "speed", "auto")

# How far off a length can be before `auto` stops nudging the speed and
# starts cutting or looping instead. A few per cent of pitch passes
# unnoticed; a quarter does not.
AUTO_SPEED_TOLERANCE = 0.15

# The reconstruction low-pass that follows the HC55516 on the board.
# Encoding anything above it spends bits on what that analog stage will
# remove anyway. It belongs to the CVSD path only: the DAC has its own
# output stage and reaches its own Nyquist limit, so band-limiting it
# here would throw away content the hardware can actually play.
VOICE_BAND = 3400.0


def resample(samples, from_rate, to_rate):
    """Resamples int16 samples, returning int16 at `to_rate`."""
    data = np.asarray(samples, dtype=np.float64)
    if data.size == 0 or from_rate == to_rate:
        return to_int16(data)

    if to_rate < from_rate:
        # Anti-alias before decimating, just under the new Nyquist limit.
        data = lowpass(data, from_rate, cutoff=0.45 * to_rate)

    count = int(round(data.size * to_rate / float(from_rate)))
    positions = np.arange(count) * (from_rate / float(to_rate))
    return to_int16(np.interp(positions, np.arange(data.size), data))


def encode_block(kind, samples):
    """Encodes samples for whichever chip plays this kind of block."""
    if kind == "dac":
        return encode_dac(samples)

    # The CVSD encoder emits one bit per sample, so the sample count
    # has to be a whole number of bytes before it can be packed.
    usable = (len(samples) // 8) * 8
    return pack_bits(Hc55516Encoder().encode(samples[:usable]))


def capacity_samples(block):
    """How many samples of audio the block holds.

    A DAC byte is one sample; a CVSD byte is eight bits, each one
    sample at the bit clock.
    """
    return block.size * 8 if block.kind == "cvsd" else block.size


def window(samples, capacity, offset):
    """Cuts or places `samples` so the result is exactly `capacity` long.

    Returns the audio, the offset actually used, and how many samples
    were dropped or added -- all in samples, which is what a slider in
    a front end needs.
    """
    count = len(samples)
    if count > capacity:
        limit = count - capacity
        offset = max(0, min(int(offset), limit))
        return samples[offset:offset + capacity], offset, limit, count - capacity, 0

    limit = capacity - count
    offset = max(0, min(int(offset), limit))
    placed = np.zeros(capacity, dtype=np.int16)
    placed[offset:offset + count] = samples
    return placed, offset, limit, 0, limit


def _choose_mode(count, capacity):
    """What `auto` does, given how far off the length is."""
    if count == capacity:
        return "trim"
    drift = abs(count - capacity) / float(capacity)
    if drift <= AUTO_SPEED_TOLERANCE:
        return "speed"
    return "trim" if count > capacity else "loop"


def _loop(samples, capacity):
    """Repeats the audio until the block is full."""
    if samples.size == 0:
        return np.zeros(capacity, dtype=np.int16)
    repeats = int(np.ceil(capacity / float(samples.size)))
    return np.tile(samples, repeats)[:capacity]


def prepare(block, samples, rate, dac_rate=11025, bit_rate=None,
            band=VOICE_BAND, offset=0, mode="trim"):
    """Makes `samples` into bytes that exactly fill `block`.

    `rate` is the WAV's own sample rate. The DAC runs at `dac_rate` and
    the CVSD bit clock at twice that unless `bit_rate` says otherwise:
    the FIRQ emits two DAC samples and four CVSD bits per interrupt,
    which ties the two together. Both are measured off the board's own
    Timer A, not guessed.

    `offset` is in samples at that native rate, and is clamped rather
    than refused -- a slider that runs past the end should stop, not
    raise. It only means anything in `trim`.
    """
    if mode not in MODES:
        raise ValueError("modo desconocido: %r (son %s)" % (mode, ", ".join(MODES)))

    native = dac_rate if block.kind == "dac" else (bit_rate or 2 * dac_rate)

    samples = resample(samples, rate, native)
    source = len(samples)
    if block.kind == "cvsd" and band and band < native / 2:
        samples = to_int16(lowpass(samples, native, cutoff=band))

    capacity = capacity_samples(block)
    if mode == "auto":
        mode = _choose_mode(source, capacity)

    speed = 1.0
    if mode == "speed" and source:
        # Resampling by the length ratio lands it exactly on the block.
        speed = source / float(capacity)
        samples = resample(samples, int(round(native * speed)) or 1, native)
        samples = samples[:capacity]
        if samples.size < capacity:
            samples = np.concatenate(
                [samples, np.zeros(capacity - samples.size, dtype=np.int16)])
    elif mode == "loop" and source:
        samples = _loop(samples, capacity)

    samples, offset, limit, dropped, added = window(samples, capacity, offset)

    data = encode_block(block.kind, samples)
    _, report = fit_payload(block, data, rate=native)
    return data, TrimReport(
        size=block.size,
        capacity_samples=capacity,
        source_samples=source,
        offset=offset,
        max_offset=limit,
        truncated=dropped,
        padded=added,
        capacity_seconds=capacity / float(native),
        source_seconds=source / float(native),
        offset_seconds=offset / float(native),
        rate=native,
        mode=mode,
        speed=speed,
    )
