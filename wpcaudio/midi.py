"""Standard MIDI files, read and written, with nothing but the stdlib.

Only what the music tools need: note on and off, tempo, and the time
division. Every other event is skipped on reading and never written.
Notes come and go as `Note(start, length, pitch, velocity, channel)`
with times in seconds, which is the unit both ends of the conversion
actually share -- the board counts in its own ticks, a MIDI file in
beats at whatever tempo it declares.
"""

import struct
from collections import namedtuple

Note = namedtuple("Note", "start length pitch velocity channel")

DEFAULT_TEMPO = 500000          # microseconds per beat: 120 bpm
DIVISION = 480                  # ticks per beat when writing


class MidiError(ValueError):
    """Not a MIDI file this reader understands."""


def _varlen(data, at):
    value = 0
    while True:
        byte = data[at]
        at += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, at


def _put_varlen(value):
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def _chunks(data):
    at = 0
    while at + 8 <= len(data):
        kind, size = data[at:at + 4], struct.unpack(">I", data[at + 4:at + 8])[0]
        yield kind, data[at + 8:at + 8 + size]
        at += 8 + size


def read(data):
    """Parses a MIDI file's bytes into a list of Notes, in seconds."""
    chunks = list(_chunks(bytes(data)))
    if not chunks or chunks[0][0] != b"MThd":
        raise MidiError("no es un fichero MIDI")
    _, tracks, division = struct.unpack(">HHH", chunks[0][1][:6])
    if division & 0x8000:
        raise MidiError("division SMPTE no soportada")

    # Gather every track's events in absolute ticks first: tempo changes
    # can sit in one track and apply to all of them.
    events, tempos = [], [(0, DEFAULT_TEMPO)]
    for kind, body in chunks[1:]:
        if kind != b"MTrk":
            continue
        at, tick, status = 0, 0, 0
        while at < len(body):
            delta, at = _varlen(body, at)
            tick += delta
            byte = body[at]
            if byte & 0x80:
                status = byte
                at += 1
            if status == 0xFF:
                meta = body[at]
                length, at = _varlen(body, at + 1)
                if meta == 0x51 and length == 3:
                    tempos.append((tick, int.from_bytes(body[at:at + 3], "big")))
                at += length
                if meta == 0x2F:
                    break
                continue
            if status in (0xF0, 0xF7):
                length, at = _varlen(body, at)
                at += length
                continue
            kind_ = status & 0xF0
            size = 1 if kind_ in (0xC0, 0xD0) else 2
            args = body[at:at + size]
            at += size
            if kind_ in (0x80, 0x90):
                velocity = args[1] if kind_ == 0x90 else 0
                events.append((tick, status & 0x0F, args[0], velocity))

    tempos.sort()

    def seconds(tick):
        total, last_tick, tempo = 0.0, 0, DEFAULT_TEMPO
        for at_tick, value in tempos:
            if at_tick > tick:
                break
            total += (at_tick - last_tick) * tempo / 1e6 / division
            last_tick, tempo = at_tick, value
        return total + (tick - last_tick) * tempo / 1e6 / division

    events.sort(key=lambda e: (e[0], e[3] > 0))
    open_, notes = {}, []
    for tick, channel, pitch, velocity in events:
        key = (channel, pitch)
        if key in open_:
            start, vel = open_.pop(key)
            notes.append(Note(start, seconds(tick) - start, pitch, vel, channel))
        if velocity:
            open_[key] = (seconds(tick), velocity)
    return sorted(notes, key=lambda n: (n.start, n.channel, n.pitch))


def write(notes, tempo=DEFAULT_TEMPO):
    """Writes Notes (in seconds) as a format 1 file, one track a channel."""
    per_tick = tempo / 1e6 / DIVISION
    channels = sorted({n.channel for n in notes})
    tracks = []

    head = _put_varlen(0) + b"\xff\x51\x03" + tempo.to_bytes(3, "big")
    tracks.append(head + _put_varlen(0) + b"\xff\x2f\x00")

    for channel in channels:
        events = []
        for n in notes:
            if n.channel != channel:
                continue
            on = int(round(n.start / per_tick))
            off = max(on + 1, int(round((n.start + n.length) / per_tick)))
            events.append((on, 1, bytes([0x90 | channel, n.pitch, n.velocity or 100])))
            events.append((off, 0, bytes([0x80 | channel, n.pitch, 0])))
        events.sort(key=lambda e: (e[0], e[1]))
        body, last = b"", 0
        for tick, _, message in events:
            body += _put_varlen(tick - last) + message
            last = tick
        tracks.append(body + _put_varlen(0) + b"\xff\x2f\x00")

    out = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), DIVISION)
    for body in tracks:
        out += b"MTrk" + struct.pack(">I", len(body)) + body
    return out
