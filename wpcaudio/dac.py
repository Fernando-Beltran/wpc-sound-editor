"""Reading the 8-bit DAC sample data on a WPC pre-DCS sound board.

These are plain unsigned samples centred on 0x80, so there is nothing
to decode beyond recentring and widening them. Byte statistics pick
these regions out easily: being a sampled waveform, adjacent bytes are
strongly correlated (see wpcaudio.romscan).
"""

import numpy as np

_CENTRE = 128


def decode_dac(data):
    """Converts unsigned 8-bit sample bytes to 16-bit signed PCM."""
    raw = np.frombuffer(data, dtype=np.uint8).astype(np.int16)
    return ((raw - _CENTRE) * 256).astype(np.int16)


def encode_dac(samples):
    """Turns 16-bit signed PCM back into the ROM's unsigned bytes."""
    raw = np.asarray(samples, dtype=np.int16).astype(np.int32)
    # Rounding rather than truncating keeps silence exactly on centre
    # and avoids a half-bit DC offset across the whole sample.
    scaled = np.right_shift(raw + 128, 8) + _CENTRE
    return np.clip(scaled, 0, 255).astype(np.uint8).tobytes()
