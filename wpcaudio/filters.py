"""The reconstruction filter that follows the HC55516 on the real board.

CVSD leaves granular quantisation noise spread across the whole band.
The sound board removes it with an analog low-pass after the decoder
chip, so a decode that skips this stage carries audible hiss even when
the waveform itself is right. Cutoff defaults to the voice band the
chip was sold for.

Implemented as cascaded Butterworth biquads so the project keeps numpy
as its only dependency.
"""

import math

import numpy as np

# Q values that make cascaded biquads Butterworth.
_BUTTERWORTH_Q = {
    2: (0.7071067811865476,),
    4: (0.5411961001461969, 1.3065629648763764),
}


def _biquad_lowpass(x, rate, cutoff, q):
    w0 = 2.0 * math.pi * cutoff / rate
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * q)

    b0 = (1.0 - cos_w0) / 2.0
    b1 = 1.0 - cos_w0
    b2 = b0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha

    b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0

    out = np.empty(x.size, dtype=np.float64)
    x1 = x2 = y1 = y2 = 0.0
    for i in range(x.size):
        xi = float(x[i])
        yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, xi
        y2, y1 = y1, yi
        out[i] = yi
    return out


def lowpass(samples, rate, cutoff=3400.0, order=4):
    """Low-pass filters samples, returning float64 of the same length."""
    if order not in _BUTTERWORTH_Q:
        raise ValueError("order must be one of %s" % sorted(_BUTTERWORTH_Q))
    if cutoff >= rate / 2:
        raise ValueError("cutoff %g is at or above the Nyquist limit" % cutoff)

    out = np.asarray(samples, dtype=np.float64)
    for q in _BUTTERWORTH_Q[order]:
        out = _biquad_lowpass(out, rate, cutoff, q)
    return out


def to_int16(samples):
    """Clips float samples into the int16 range."""
    return np.clip(np.asarray(samples, dtype=np.float64), -32768, 32767).astype(np.int16)
