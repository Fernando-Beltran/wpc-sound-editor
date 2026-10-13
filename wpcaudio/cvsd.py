"""Continuously variable slope delta (CVSD) codec for the Harris HC55516.

Each bit carries only "up" or "down"; the step size adapts instead. A
run of identical bits means the signal is moving faster than the current
step can follow, so the syllabic filter charges the step up; a mixed run
lets it decay back toward the floor. The encoder is the decoder plus one
comparison: emit the bit that moves the reconstruction toward the target,
then run the identical state update, so both sides stay in lockstep.

The two time constants are what make or break this. They are taken
from PinMAME's `src/sound/hc55516.c`, which derives them from the data
sheet: the syllabic filter charges with a 4 ms time constant and the
signal estimator -- the integrator -- leaks with a 1 ms one, both
quoted at a 16 kHz clock. The data sheet notes that both are inversely
proportional to the clock, so the per-bit factors are constants and do
not depend on the bit rate.

Two earlier versions got this wrong in opposite directions. The first
mirrored the chip's 10-bit accumulator with a step ceiling as large as
the whole range, so the step exploded on the first coincidence and
turned any input into a full-scale square wave. The second tamed the
step but left the integrator leaking at 0.999 per bit -- a 45 ms time
constant, sixty times too slow -- so DC accumulated and every real
voice block decoded hot and clipped. The leak is the parameter that
matters: 0.9394 per bit, about 6% of the level gone with each bit.

The level is in the chip's own volts: the syllabic filter charges
toward 1.7 V, which the data sheet gives as the peak output, and the
integrator settles below that. `GAIN` maps that excursion onto int16,
so nothing clips unless the data really does reach full scale.

The bit clock still needs measuring but it changes
pitch and duration only, not these factors.
"""

import numpy as np

_INT16_MIN = -32768.0
_INT16_MAX = 32767.0

# Per-bit filter factors, from PinMAME's hc55516.c.
CHARGE = 0.984496437005408453   # syllabic filter, 4 ms at 16 kHz
DECAY = 0.939413062813475808    # signal estimator, 1 ms at 16 kHz

# The syllabic filter's two input levels. Coincidence pulls the step
# toward the chip's peak output voltage; anything else pulls it to rest.
V_HIGH = 1.7
V_LOW = 0.0

# Volts to int16. The integrator settles at V_HIGH * DECAY at most, so
# this leaves a little headroom rather than clipping at the top.
GAIN = _INT16_MAX / V_HIGH


class _Hc55516Core:
    """Shared adaptive-step state machine behind the encoder and decoder."""

    def __init__(self, coincidence_bits=3, charge=CHARGE, decay=DECAY,
                 v_high=V_HIGH, v_low=V_LOW, gain=GAIN):
        self.coincidence_bits = coincidence_bits
        self.charge = charge
        self.decay = decay
        self.v_high = v_high
        self.v_low = v_low
        self.gain = gain
        self._mask = (1 << coincidence_bits) - 1
        self.reset()

    def reset(self):
        """Clears the state, as a chip reset would."""
        self._shift = 0
        self._syllabic = 0.0
        self._integrator = 0.0

    @property
    def output(self):
        """The current reconstructed sample, in int16 units."""
        return self._scaled()

    def _scaled(self):
        value = self._integrator * self.gain
        if value > _INT16_MAX:
            return int(_INT16_MAX)
        if value < _INT16_MIN:
            return int(_INT16_MIN)
        return int(value)

    def step(self, bit):
        """Feeds one bit through the state machine and returns the sample.

        The order is the chip's: the integrator moves by the step the
        syllabic filter held *before* this bit, then leaks, and only
        then does the shift register take the bit and the syllabic
        filter re-charge from it.
        """
        delta = (1.0 - self.decay) * self._syllabic
        self._integrator += delta if bit else -delta
        self._integrator *= self.decay

        self._shift = ((self._shift << 1) | bit) & self._mask
        coincident = self._shift == 0 or self._shift == self._mask

        self._syllabic *= self.charge
        self._syllabic += (1.0 - self.charge) * (self.v_high if coincident else self.v_low)

        return self._scaled()


class Hc55516Decoder(_Hc55516Core):
    """Turns an HC55516 bitstream into 16-bit PCM, one sample per bit."""

    def decode(self, bits):
        bits = np.asarray(bits, dtype=np.uint8)
        out = np.empty(bits.size, dtype=np.int16)
        for i in range(bits.size):
            out[i] = self.step(int(bits[i]))
        return out


class Hc55516Encoder(_Hc55516Core):
    """Turns 16-bit PCM into an HC55516 bitstream, one bit per sample."""

    def encode(self, samples):
        samples = np.asarray(samples, dtype=np.int16)
        bits = np.empty(samples.size, dtype=np.uint8)
        for i in range(samples.size):
            bit = 1 if int(samples[i]) >= self.output else 0
            bits[i] = bit
            self.step(bit)
        return bits


def unpack_bits(data, msb_first=True):
    """Expands bytes into one array element per bit."""
    raw = np.frombuffer(data, dtype=np.uint8)
    return np.unpackbits(raw, bitorder="big" if msb_first else "little")


def pack_bits(bits, msb_first=True):
    """Packs one-bit-per-element back into bytes."""
    raw = np.asarray(bits, dtype=np.uint8)
    if raw.size % 8:
        raise ValueError("hacen falta bits enteros por byte, sobran %d" % (raw.size % 8))
    return np.packbits(raw, bitorder="big" if msb_first else "little").tobytes()
