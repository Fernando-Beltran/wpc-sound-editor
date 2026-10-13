"""Writing a YM2151 register log out as a VGM file.

VGM is the standard log format for chip music: a header naming the
chips and their clocks, then a stream of register writes separated by
waits. Players for it have existed for twenty years and already carry
accurate YM2151 cores, so exporting to VGM gets this board's music
audible without writing an FM synthesiser -- which is the one piece of
this project that would otherwise have to be built from scratch.

The log itself comes from `wpcaudio.board`: run U18's own program,
record every write it makes to `$2400`/`$2401`. On a pre-DCS board that
stream *is* the music; nothing is recorded as audio.
"""

import struct

# VGM counts time in samples at a fixed 44.1 kHz, whatever the chips run at.
VGM_SAMPLE_RATE = 44100

VERSION = 0x151
HEADER_SIZE = 0x100
DATA_OFFSET_FIELD = 0x34

# Commands used here. 0x61 takes a 16-bit sample count.
WRITE_YM2151 = 0x54
WAIT = 0x61
END = 0x66
MAX_WAIT = 0xFFFF


def _wait(samples):
    """Encodes a pause, split across commands if it is a long one."""
    out = bytearray()
    while samples > 0:
        chunk = min(samples, MAX_WAIT)
        out += bytes([WAIT]) + struct.pack("<H", chunk)
        samples -= chunk
    return out


def build_vgm(writes, cpu_clock, ym_clock, tail_samples=VGM_SAMPLE_RATE // 2):
    """Builds the bytes of a VGM file from a register log.

    `writes` is a sequence of `(cycle, register, value)` in CPU cycles,
    in order. `tail_samples` leaves a little silence at the end so a
    player does not cut the last note off.
    """
    body = bytearray()
    previous_cycle = 0
    elapsed = 0

    for write in writes:
        if write.cycle < previous_cycle:
            raise ValueError("las escrituras deben venir en orden: %d tras %d"
                             % (write.cycle, previous_cycle))
        samples = int(write.cycle * VGM_SAMPLE_RATE / cpu_clock) - elapsed
        if samples > 0:
            body += _wait(samples)
            elapsed += samples
        body += bytes([WRITE_YM2151, write.register & 0xFF, write.value & 0xFF])
        previous_cycle = write.cycle

    if tail_samples > 0:
        body += _wait(tail_samples)
        elapsed += tail_samples
    body += bytes([END])

    header = bytearray(HEADER_SIZE)
    header[0x00:0x04] = b"Vgm "
    struct.pack_into("<I", header, 0x04, HEADER_SIZE + len(body) - 0x04)
    struct.pack_into("<I", header, 0x08, VERSION)
    struct.pack_into("<I", header, 0x18, elapsed)
    struct.pack_into("<I", header, 0x30, ym_clock)
    struct.pack_into("<I", header, DATA_OFFSET_FIELD, HEADER_SIZE - DATA_OFFSET_FIELD)
    return bytes(header) + bytes(body)


def write_vgm(path, writes, cpu_clock, ym_clock, **options):
    """Writes the log to `path` and returns it."""
    with open(str(path), "wb") as handle:
        handle.write(build_vgm(writes, cpu_clock, ym_clock, **options))
    return path
