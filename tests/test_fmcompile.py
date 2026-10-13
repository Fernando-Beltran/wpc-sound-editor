"""Spec for compiling MIDI notes into the board's music scripts."""

from collections import namedtuple

import pytest

from wpcaudio import fmcompile
from wpcaudio.fmcompile import Voice, ticks
from wpcaudio.fmmidi import kc_to_midi, midi_to_kc
from wpcaudio.midi import Note

Block = namedtuple("Block", "bank start end")
BEAT = 0.5


def test_key_codes_and_midi_notes_agree_both_ways():
    assert midi_to_kc(69) == 0x4A                # A4 = 440 Hz
    assert kc_to_midi(0x56) == 78
    for pitch in range(13, 109):
        if midi_to_kc(pitch) is not None:
            assert kc_to_midi(midi_to_kc(pitch)) == pitch


def test_notes_outside_the_chip_are_refused():
    assert midi_to_kc(0) is None
    assert midi_to_kc(127) is None


def test_a_short_note_takes_a_one_byte_duration():
    data, _ = fmcompile.stream([Note(0, 0.1, 69, 100, 0)], base=0x5000, loop=False)

    length = ticks(0.1) + fmcompile.RELEASE_GAP
    assert data == bytes([0xBB, 1, 0xBA, 0x4A | 0x80, length, 0x95])


def test_a_long_note_takes_two_bytes_without_bit_7():
    data, _ = fmcompile.stream([Note(0, 2.0, 69, 100, 0)], base=0x5000, loop=False)

    length = ticks(2.0) + 1
    assert data[2:6] == bytes([0xBA, 0x4A, length >> 8, length & 0xFF])


def test_a_gap_between_notes_becomes_a_rest():
    notes = [Note(0, 0.1, 60, 100, 0), Note(0.5, 0.1, 62, 100, 0)]

    data, _ = fmcompile.stream(notes, base=0x5000, loop=False)

    rest = ticks(0.5) - (ticks(0.1) + 1)
    assert bytes([0xB5, rest]) in data


def test_a_looping_voice_ends_with_a_goto_past_its_gate():
    data, _ = fmcompile.stream([Note(0, 0.1, 60, 100, 0)], base=0x6000)

    assert data[-3:] == bytes([0xB2, 0x60, 0x02])


def test_voices_are_padded_to_the_song_length():
    data, _ = fmcompile.stream([Note(0, 0.1, 60, 100, 0)], base=0x6000,
                               total=ticks(0.4), loop=False)

    # note, then a rest out to 0.4 s, then stop
    assert data[-1] == 0x95
    assert data[-3] == 0xB5


def test_a_chord_is_spread_over_voices():
    voices = [Voice(0x8000, 0, 0x8003), Voice(0x8100, 1, 0x8103)]
    chord = [Note(0, BEAT, 60, 100, 0), Note(0, BEAT, 64, 100, 0)]

    shares, dropped = fmcompile.allocate(chord, voices)

    assert sorted(len(s) for s in shares.values()) == [1, 1]
    assert dropped == []


def test_more_notes_than_voices_are_dropped_and_reported():
    voices = [Voice(0x8000, 0, 0x8003)]
    chord = [Note(0, BEAT, 60, 100, 0), Note(0, BEAT, 64, 100, 0)]

    shares, dropped = fmcompile.allocate(chord, voices)

    assert len(dropped) == 1


def test_a_part_stays_on_the_voice_that_started_it():
    voices = [Voice(0x8000, 0, 0x8003), Voice(0x8100, 1, 0x8103)]
    notes = [Note(0, BEAT, 60, 100, 1), Note(0, BEAT, 48, 100, 2),
             Note(BEAT, BEAT, 62, 100, 1)]

    shares, _ = fmcompile.allocate(notes, voices)

    melody = [s for s in shares.values() if s and s[0].channel == 1][0]
    assert [n.pitch for n in melody] == [60, 62]


def test_drums_are_left_to_the_samples():
    voices = [Voice(0x8000, 0, 0x8003)]

    shares, dropped = fmcompile.allocate([Note(0, BEAT, 36, 100, 9)], voices)

    assert shares == {0: []} and dropped == []


def test_the_song_jumps_from_each_prologue_into_the_block():
    voices = [Voice(0x8000, 0, 0x8006), Voice(0x8100, 1, 0x8103)]
    notes = [Note(0, BEAT, 60, 100, 0)]

    plan = fmcompile.compile_song(notes, voices, Block(0xB2, 0x4002, 0x5000))

    assert (0x8006, bytes([0xBE, 0xB2, 0x40, 0x02])) in plan.writes
    assert (0x8103, bytes([0x95])) in plan.writes      # the idle part stops
    assert plan.streams[0][0] == 0x4002


def test_a_song_too_big_for_its_block_is_refused():
    voices = [Voice(0x8000, 0, 0x8003)]
    notes = [Note(i * 0.1, 0.05, 60, 100, 0) for i in range(100)]

    with pytest.raises(ValueError):
        fmcompile.compile_song(notes, voices, Block(0xB2, 0x4002, 0x4040))


def test_voices_sharing_a_set_up_end_play_one_part():
    voices = [Voice(0x7FB7, 4, 0x7FC0), Voice(0x7FBA, 5, 0x7FC0), Voice(0x8100, 1, 0x8103)]
    chord = [Note(0, BEAT, 60, 100, 0), Note(0, BEAT, 64, 100, 0), Note(0, BEAT, 67, 100, 0)]

    plan = fmcompile.compile_song(chord, voices, Block(0xB2, 0x4002, 0x5000))

    jumps = [w for w in plan.writes if w[1][0] == 0xBE]
    assert sorted(a for a, _ in jumps) == [0x7FC0, 0x8103]
    assert len(plan.dropped) == 1                # two parts, three notes


def test_programs_to_silence_are_stopped_at_their_first_byte():
    voices = [Voice(0x8000, 0, 0x8003)]

    plan = fmcompile.compile_song([Note(0, BEAT, 60, 100, 0)], voices,
                                  Block(0xB2, 0x4002, 0x5000), silence=[0x8026])

    assert (0x8026, bytes([0x95])) in plan.writes


def test_the_priority_channel_keeps_its_notes_when_voices_run_out():
    voices = [Voice(0x8000, 0, 0x8003)]
    accompaniment = Note(0, 4 * BEAT, 48, 100, 3)      # starts first
    tune = Note(BEAT, BEAT, 72, 100, 0)

    shares, dropped = fmcompile.allocate([accompaniment, tune], voices, priority=[0, 3])

    assert shares[0] == [tune] and dropped == [accompaniment]


def test_a_later_channel_fills_the_gaps():
    voices = [Voice(0x8000, 0, 0x8003)]
    tune = [Note(0, BEAT, 72, 100, 0), Note(3 * BEAT, BEAT, 74, 100, 0)]
    fill = Note(BEAT, BEAT, 48, 100, 3)

    shares, dropped = fmcompile.allocate(tune + [fill], voices)

    assert [n.pitch for n in shares[0]] == [72, 48, 74] and dropped == []


def test_a_chosen_instrument_replaces_the_set_up():
    voices = [Voice(0x8000, 0, 0x8006)]
    instrument = bytes([0x93, 0x5D, 0x8A, 0xBB, 0x0F])

    plan = fmcompile.compile_song([Note(0, BEAT, 60, 100, 0)], voices,
                                  Block(0xB2, 0x4002, 0x5000), instruments={0: instrument})

    assert (0x8000, instrument + bytes([0xBE, 0xB2, 0x40, 0x02])) in plan.writes


def test_a_unison_pair_takes_its_instrument_where_the_code_joins():
    voices = [Voice(0x7FB7, 4, 0x7FC0), Voice(0x7FBA, 5, 0x7FC0)]

    plan = fmcompile.compile_song([Note(0, BEAT, 60, 100, 0)], voices,
                                  Block(0xB2, 0x4002, 0x5000), instruments={0: bytes([0x93, 0x5D, 0x8A])})

    assert [a for a, d in plan.writes if d[-4] == 0xBE] == [0x7FBA]


def test_an_instrument_too_big_for_its_place_is_refused():
    voices = [Voice(0x8000, 0, 0x8003)]

    with pytest.raises(ValueError):
        fmcompile.compile_song([Note(0, BEAT, 60, 100, 0)], voices,
                               Block(0xB2, 0x4002, 0x5000), instruments={0: bytes(10)},
                               room=lambda address: 6)


def _scale(count, channel=0, start=0.0):
    return [Note(start + i * BEAT, BEAT * 0.8, 60 + i % 12, 100, channel) for i in range(count)]


def test_a_song_that_fits_comes_back_untouched():
    voices = [Voice(0x8000, 0, 0x8003)]
    song = _scale(8)

    fitted, report = fmcompile.fit_song(song, voices, 10000)

    assert fitted == song and report["cut_at"] is None and report["spare"] > 0


def test_cutting_keeps_the_longest_opening_that_fits():
    voices = [Voice(0x8000, 0, 0x8003)]
    song = _scale(100)
    size = fmcompile.measure(song[:40], voices)

    fitted, report = fmcompile.fit_song(song, voices, size, mode="cut")

    assert fmcompile.measure(fitted, voices) <= size
    assert 35 <= len(fitted) <= 41
    assert report["cut_at"] == pytest.approx(len(fitted) * BEAT)


def test_thinning_drops_the_lowest_priority_channel_first():
    voices = [Voice(0x8000, 0, 0x8003), Voice(0x8100, 1, 0x8103)]
    tune, filler = _scale(20, channel=0), _scale(20, channel=5)
    size = fmcompile.measure(tune, voices) + 4

    fitted, report = fmcompile.fit_song(tune + filler, voices, size, mode="thin",
                                        priority=[0, 5])

    assert report["removed"] == [5] and report["cut_at"] is None
    assert {n.channel for n in fitted} == {0}


def test_the_allocator_is_unchanged_by_the_faster_overlap_check():
    voices = [Voice(0x8000, 0, 0x8003), Voice(0x8100, 1, 0x8103)]
    notes = [Note(0, 1.0, 60, 100, 0), Note(0.5, 1.0, 62, 100, 0), Note(1.0, 0.5, 64, 100, 0),
             Note(0.2, 0.2, 40, 100, 1)]

    shares, dropped = fmcompile.allocate(notes, voices)

    # 60 and 64 share the first voice back to back, 62 takes the second,
    # and the short 40 fits the second voice's silence before 62 starts.
    assert dropped == []
    assert sorted(n.pitch for n in shares[0]) == [60, 64]
    assert sorted(n.pitch for n in shares[1]) == [40, 62]
