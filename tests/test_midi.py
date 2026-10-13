"""Spec for the minimal MIDI reader and writer."""

import pytest

from wpcaudio import midi
from wpcaudio.midi import Note


def test_notes_survive_a_round_trip():
    notes = [Note(0.0, 0.5, 60, 100, 0), Note(0.5, 0.25, 64, 90, 0),
             Note(0.25, 1.0, 48, 80, 1)]

    back = midi.read(midi.write(notes))

    assert [(n.pitch, n.channel, n.velocity) for n in back] == \
        [(60, 0, 100), (48, 1, 80), (64, 0, 90)]
    for got, want in zip(sorted(back), sorted(notes)):
        assert got.start == pytest.approx(want.start, abs=0.002)
        assert got.length == pytest.approx(want.length, abs=0.002)


def test_tempo_sets_the_seconds():
    # At 60 bpm a 480-tick beat is a second, so the same file read at a
    # different declared tempo gives different times.
    data = midi.write([Note(1.0, 1.0, 60, 100, 0)], tempo=1000000)

    (note,) = midi.read(data)

    assert note.start == pytest.approx(1.0, abs=0.002)
    assert note.length == pytest.approx(1.0, abs=0.002)


def test_a_note_on_with_zero_velocity_ends_a_note():
    track = bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x90, 60, 0, 0x00, 0xFF, 0x2F, 0x00])
    data = (b"MThd" + bytes([0, 0, 0, 6, 0, 0, 0, 1, 0x01, 0xE0])
            + b"MTrk" + len(track).to_bytes(4, "big") + track)

    (note,) = midi.read(data)

    assert note.length == pytest.approx(0.5, abs=0.002)


def test_running_status_is_understood():
    track = bytes([0x00, 0x90, 60, 100, 0x00, 62, 100,
                   0x83, 0x60, 60, 0, 0x00, 62, 0, 0x00, 0xFF, 0x2F, 0x00])
    data = (b"MThd" + bytes([0, 0, 0, 6, 0, 0, 0, 1, 0x01, 0xE0])
            + b"MTrk" + len(track).to_bytes(4, "big") + track)

    assert [n.pitch for n in midi.read(data)] == [60, 62]


def test_something_else_is_refused():
    with pytest.raises(midi.MidiError):
        midi.read(b"RIFF....WAVE")
