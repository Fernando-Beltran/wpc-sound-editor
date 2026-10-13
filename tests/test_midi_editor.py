"""Spec for the editor's MIDI choices and the instrument catalogue."""

import importlib.util
import pathlib

from wpcide.server import _midi_options

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "instrument_catalog", ROOT / "scripts" / "instrument_catalog.py")
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


def test_the_page_choices_are_read_from_the_query():
    options = _midi_options({"block": ["dac:17"], "priority": ["1,2, 5"],
                             "inst": ["0:3,5:12"], "loop": ["0"], "drums": ["1"]})

    assert options == {"block": "dac:17", "priority": ["1", "2", " 5"],
                       "instruments": {0: 3, 5: 12}, "loop": False, "keep_drums": True,
                       "fit": "cut"}


def test_missing_choices_take_the_defaults():
    options = _midi_options({"block": ["cvsd:3"]})

    assert options["priority"] is None and options["instruments"] == {}
    assert options["loop"] is True and options["keep_drums"] is False


def test_an_instrument_is_what_it_loads_not_its_volume():
    plain = bytes([0x93, 0xB3, 0xAE])
    louder = plain + bytes([0x8F, 0x04])

    assert catalog.signature(plain) == catalog.signature(louder)


def test_a_set_up_that_loads_nothing_is_no_instrument():
    assert catalog.signature(bytes([0xA8, 0xFF, 0xE8])) == ()


def test_variants_condense_into_the_most_used_one():
    raw = [{"bytes": "93b3ae", "uses": [1, 2, 3]},
           {"bytes": "93b3ae8f04", "uses": [4]},
           {"bytes": "a8ffe8", "uses": [5, 6]}]

    (only,) = catalog.condense(raw)

    assert only["bytes"] == "93b3ae" and only["variants"] == 2 and len(only["uses"]) == 4
