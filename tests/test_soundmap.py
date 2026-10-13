"""Spec for the sound map: which command plays which block."""

import json

import pytest

from wpcaudio.soundmap import SoundMap

SCAN = [
    {"cmd": 0x00, "writes": 0, "keyon": 0, "samples": [], "unknown": 0},
    {"cmd": 0x06, "writes": 1059, "keyon": 136,
     "samples": ["dac:1", "dac:0", "dac:8"], "unknown": 0},
    {"cmd": 0x0E, "writes": 769, "keyon": 244, "samples": ["dac:0"], "unknown": 0},
    {"cmd": 0x40, "writes": 120, "keyon": 0, "samples": ["cvsd:12"], "unknown": 2},
]


@pytest.fixture
def smap():
    return SoundMap.from_scan(SCAN)


def test_a_block_knows_which_commands_play_it(smap):
    assert smap.commands_for("dac", 0) == [0x06, 0x0E]
    assert smap.commands_for("cvsd", 12) == [0x40]


def test_a_block_nobody_plays_has_no_commands(smap):
    assert smap.commands_for("dac", 99) == []


def test_a_command_knows_what_it_plays_in_order(smap):
    # The order is the order the board started them, which is the order
    # they are heard; it is not sorted.
    assert smap.sounds_for(0x06) == [("dac", 1), ("dac", 0), ("dac", 8)]


def test_a_command_that_only_makes_music_is_marked_as_such(smap):
    assert smap.is_music(0x0E) is True
    assert smap.is_music(0x40) is False
    assert smap.is_silent(0x00) is True


def test_coverage_counts_the_blocks_the_scan_reached(smap):
    summary = smap.coverage([("dac", i) for i in range(10)] + [("cvsd", 12)])

    assert summary["reached"] == 4            # dac 0, 1, 8 and cvsd 12
    assert summary["total"] == 11
    assert summary["unreached"] == [("dac", 2), ("dac", 3), ("dac", 4),
                                    ("dac", 5), ("dac", 6), ("dac", 7),
                                    ("dac", 9)]


def test_unidentified_starts_are_carried_not_hidden(smap):
    """A stream the scan saw but could not match to a table entry.

    Worth surfacing rather than dropping: it means either the watcher
    or the directory is wrong about something, and a silent zero would
    hide that.
    """
    assert smap.unknown_starts == 2


def test_a_map_survives_a_round_trip_through_json(smap, tmp_path):
    path = tmp_path / "mapa.json"
    path.write_text(json.dumps(SCAN), encoding="utf-8")

    again = SoundMap.load(path)

    assert again.commands_for("dac", 0) == smap.commands_for("dac", 0)
    assert again.sounds_for(0x06) == smap.sounds_for(0x06)


def test_a_missing_map_is_an_empty_one_not_a_crash(tmp_path):
    empty = SoundMap.load(tmp_path / "no-existe.json")

    assert empty.commands_for("dac", 0) == []
    assert empty.is_empty is True


def test_rows_that_failed_in_the_scan_are_skipped():
    smap = SoundMap.from_scan([{"cmd": 7, "error": "se colgo"}])

    assert smap.commands_for("dac", 0) == []
    assert smap.failed == [7]
