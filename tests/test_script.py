"""Spec for finding a command's voice programs and repointing them."""

import pytest

from wpcaudio import script


def _image():
    """A U18-shaped image with one kind 1 and one kind 4 command."""
    image = bytearray(0x80000)
    page = script.DIRECTORY_PAGE

    def put(address, payload):
        # Not `image[start:][:n] = ...`: slicing a bytearray makes a copy,
        # so that writes to the copy and leaves the image untouched.
        start = script.rom_offset(image, address)
        image[start:start + len(payload)] = payload

    def word(address, value):
        put(address, bytes([value >> 8, value & 0xFF]))

    word(script.COMMAND_TABLE, 0x5000)      # the command table
    word(script.KIND4_TABLE, 0x6000)        # kind 4's script table
    word(script.KIND1_TABLE, 0x7000)        # kind 1's

    # Command $10: kind 4, parameter 0 -> header with two voices.
    put(0x5000 + 0x10 * 2, bytes([4, 0]))
    word(0x6000, 0x6100)
    put(0x6100, bytes([0x03]))              # mask: two voices
    word(0x6101, 0x8000)
    word(0x6103, 0x8100)

    # Command $11: kind 1, parameter 0 -> ten pointers, some repeated.
    put(0x5000 + 0x11 * 2, bytes([1, 0]))
    word(0x7000, 0x7100)
    for i in range(script.KIND1_VOICES):
        word(0x7100 + i * 2, 0x9000 if i < 8 else 0x9100)

    # Command $12: kind 15, which runs no programs at all.
    put(0x5000 + 0x12 * 2, bytes([15, 0]))
    return image


def test_a_kind_4_command_lists_one_program_per_voice():
    programs = script.programs_of(_image(), 0x10)

    assert [p.address for p in programs] == [0x8000, 0x8100]


def test_repeated_pointers_are_listed_once():
    programs = script.programs_of(_image(), 0x11)

    assert [p.address for p in programs] == [0x9000, 0x9100]


def test_a_program_address_resolves_into_the_image():
    programs = script.programs_of(_image(), 0x10)

    assert programs[0].offset == script.DIRECTORY_PAGE + 0x8000 - 0x4000


def test_the_fixed_bank_resolves_to_the_top_of_the_chip():
    image = bytearray(0x80000)

    assert script.rom_offset(image, 0xC000) == 0x7C000
    assert script.rom_offset(image, 0xFFFF) == 0x7FFFF


def test_a_command_that_runs_no_programs_is_refused():
    with pytest.raises(script.NoPrograms):
        script.programs_of(_image(), 0x12)


# -- the replacement script -------------------------------------------


def test_one_block_becomes_three_bytes():
    assert script.play_cvsd_bytes([0x2A]) == b"\x00\x2a\xff"


def test_several_blocks_play_in_the_order_given():
    assert script.play_cvsd_bytes([5, 3, 9]) == b"\x00\x05\x03\x09\xff"


def test_an_empty_list_is_refused():
    with pytest.raises(ValueError):
        script.play_cvsd_bytes([])


def test_the_terminator_cannot_be_used_as_an_index():
    # $FF ends the list, so a block numbered $FF could never be reached.
    with pytest.raises(ValueError):
        script.play_cvsd_bytes([0xFF])


# -- the patch ---------------------------------------------------------


def test_the_patch_turns_one_program_into_the_play_list():
    image = _image()

    patch = script.recording_patch(image, 0x10, [7, 8])

    host = patch.host.offset
    assert (host, b"\x00\x07\x08\xff\x15") in patch.writes


def test_every_other_program_is_silenced():
    image = _image()

    patch = script.recording_patch(image, 0x10, [7])

    silenced = [w for w in patch.writes if w[1] == b"\x15"]
    assert len(silenced) == 1


def test_the_list_goes_where_there_is_room():
    # $8000 is followed by $8100, so it has 256 bytes; $8100 has the
    # rest of the window. The longer list must go to the roomier one.
    patch = script.recording_patch(_image(), 0x10, [1])

    assert patch.host.address == 0x8100


def test_a_list_that_would_overrun_the_next_program_is_refused():
    image = _image()
    users = {0x8000: {0x10}, 0x8100: {0x10}, 0x8104: {0x40}, 0x8003: {0x41}}

    with pytest.raises(ValueError):
        script.recording_patch(image, 0x10, [1, 2], users=users)


def test_other_commands_sharing_a_program_are_named():
    image = _image()
    users = {0x8000: {0x10, 0x33}, 0x8100: {0x10}}

    patch = script.recording_patch(image, 0x10, [1], users=users)

    assert patch.shared == [0x33]


def test_a_dac_block_lasts_its_size_in_ticks():
    # 22 bytes a tick: 2 samples per FIRQ, one tick every 11 FIRQs.
    assert script.dac_ticks(22) == 1
    assert script.dac_ticks(23) == 2
    assert script.dac_ticks(5746) == 262


def test_dac_blocks_play_one_flagged_opcode_each():
    listing = script.play_dac_bytes([(1, 5746), (3, 1666)])

    assert listing == bytes([0x88, 1, 0x01, 0x06, 0x88, 3, 0x00, 0x4C, 0x95])


def test_a_dac_patch_uses_the_dac_listing():
    patch = script.recording_patch(_image(), 0x10, [4], kind="dac", sizes=[44])

    assert (patch.host.offset, bytes([0x88, 4, 0, 2, 0x95])) in patch.writes


def test_apply_returns_what_it_overwrote():
    image = _image()
    image[100:103] = b"abc"

    before = script.apply(image, [(100, b"XY")])

    assert image[100:103] == b"XYc"
    assert before == [(100, b"ab")]


def test_a_window_program_never_reaches_the_checksum_byte():
    assert script.room(0xBF00, []) == 0xFF
