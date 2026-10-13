"""Spec for the sound ROM directory: resolving banks, reading the sample
tables, extracting a block and writing a replacement back in place."""

import pytest

from wpcaudio.soundrom import (
    Block,
    CVSD_SILENCE,
    DAC_SILENCE,
    SoundRom,
    fit_payload,
)

U18_SIZE = 0x80000
U15_SIZE = 0x80000
U14_SIZE = 0x20000


def _blank_rom():
    return SoundRom(
        u18=bytearray(U18_SIZE), u15=bytearray(U15_SIZE), u14=bytearray(U14_SIZE)
    )


# -- bank resolution ------------------------------------------------


def test_the_directory_bank_maps_to_u18():
    # Bank $7C selects U18 (top three bits) page 0x0C (low nibble), and
    # the window starts at $4000, so $4003 is the fourth byte of the page.
    assert _blank_rom().resolve(0x7C, 0x4003) == ("u18", 0x060003)


def test_a_u15_bank_maps_into_u15():
    assert _blank_rom().resolve(0xA0, 0x4000) == ("u15", 0x000000)


def test_u14_is_mirrored_because_the_chip_is_smaller_than_its_slot():
    # U14 holds 128 KB in a 512 KB slot, so PinMAME reloads it four
    # times over; page 0x0D wraps back to 0x08000 inside the chip.
    assert _blank_rom().resolve(0xDD, 0x7DC5) == ("u14", 0x00BDC5)


def test_an_address_outside_the_window_is_refused():
    with pytest.raises(ValueError):
        _blank_rom().resolve(0x7C, 0xC000)


def test_an_unknown_chip_select_is_refused():
    with pytest.raises(ValueError):
        _blank_rom().resolve(0x00, 0x4000)


# -- reading the tables ---------------------------------------------


def _rom_with_tables():
    """Builds a ROM carrying one DAC block and one CVSD block.

    The layout mirrors what the U18 disassembly showed: a header in the
    directory bank whose word at $4003 points at an array of 5-byte
    `{bank, start, end}` records, and whose word at $4015 points at an
    array of 16-bit pointers, each to one such record.
    """
    rom = _blank_rom()

    def put(bank, addr, payload):
        chip, offset = rom.resolve(bank, addr)
        rom.chips[chip][offset:offset + len(payload)] = payload

    # One DAC record at $5000, and a terminator the parser will reject.
    put(0x7C, 0x4003, bytes([0x50, 0x00]))
    put(0x7C, 0x5000, bytes([0x7C, 0x60, 0x00, 0x60, 0x40]))
    put(0x7C, 0x5005, bytes([0x00, 0x00, 0x00, 0x00, 0x00]))
    put(0x7C, 0x6000, bytes(range(64)))

    # One CVSD record at $5200, reached through the pointer table at $5100.
    put(0x7C, 0x4015, bytes([0x51, 0x00]))
    put(0x7C, 0x5100, bytes([0x52, 0x00]))
    put(0x7C, 0x5102, bytes([0x00, 0x00]))
    put(0x7C, 0x5200, bytes([0xA0, 0x40, 0x00, 0x40, 0x20]))
    put(0xA0, 0x4000, bytes(range(32)))
    return rom


def test_the_dac_table_is_read_as_blocks():
    blocks = [b for b in _rom_with_tables().blocks() if b.kind == "dac"]

    assert len(blocks) == 1
    block = blocks[0]
    assert (block.index, block.bank, block.start, block.end) == (0, 0x7C, 0x6000, 0x6040)
    assert block.size == 64
    assert (block.chip, block.offset) == ("u18", 0x062000)


def test_the_cvsd_table_is_read_through_its_pointer_table():
    blocks = [b for b in _rom_with_tables().blocks() if b.kind == "cvsd"]

    assert len(blocks) == 1
    block = blocks[0]
    assert (block.bank, block.start, block.end, block.size) == (0xA0, 0x4000, 0x4020, 32)
    assert block.chip == "u15"


def test_a_block_extracts_exactly_the_bytes_between_start_and_end():
    rom = _rom_with_tables()
    dac = next(b for b in rom.blocks() if b.kind == "dac")

    assert rom.extract(dac) == bytes(range(64))


# -- writing a replacement back -------------------------------------


def test_a_shorter_payload_is_padded_with_silence_and_reported():
    rom = _rom_with_tables()
    dac = next(b for b in rom.blocks() if b.kind == "dac")

    data, report = fit_payload(dac, b"\x01\x02\x03")

    assert len(data) == 64, "el bloque no puede cambiar de tamano"
    assert data[3:] == bytes([DAC_SILENCE]) * 61
    assert report.padded == 61
    assert report.truncated == 0


def test_a_longer_payload_is_truncated_and_reported():
    rom = _rom_with_tables()
    dac = next(b for b in rom.blocks() if b.kind == "dac")

    data, report = fit_payload(dac, bytes(100))

    assert len(data) == 64
    assert report.truncated == 36
    assert report.padded == 0


def test_cvsd_silence_is_an_alternating_bit_pattern():
    rom = _rom_with_tables()
    cvsd = next(b for b in rom.blocks() if b.kind == "cvsd")

    data, _ = fit_payload(cvsd, b"")

    # A CVSD stream at rest alternates up and down, which is why 0x55
    # and 0xAA dominate the byte histogram of a real voice region.
    assert data == bytes([CVSD_SILENCE]) * 32


def test_writing_a_block_leaves_the_rest_of_the_rom_alone():
    rom = _rom_with_tables()
    dac = next(b for b in rom.blocks() if b.kind == "dac")
    before = bytes(rom.chips["u18"])

    rom.write(dac, b"\xaa" * 64)

    after = bytes(rom.chips["u18"])
    assert rom.extract(dac) == b"\xaa" * 64
    assert after[:dac.offset] == before[:dac.offset]
    assert after[dac.offset + dac.size:] == before[dac.offset + dac.size:]


def test_writing_more_than_the_block_holds_is_refused():
    rom = _rom_with_tables()
    dac = next(b for b in rom.blocks() if b.kind == "dac")

    with pytest.raises(ValueError):
        rom.write(dac, b"\x00" * 65)


def test_the_report_states_the_duration_the_block_can_hold():
    rom = _rom_with_tables()
    dac = next(b for b in rom.blocks() if b.kind == "dac")

    _, report = fit_payload(dac, b"\x01" * 32, rate=8000)

    # 64 bytes of DAC at 8 kHz is 8 ms; the payload fills half of it.
    assert report.capacity_seconds == pytest.approx(0.008)
    assert report.payload_seconds == pytest.approx(0.004)


def test_blocks_that_sit_back_to_back_form_one_run():
    """Blocks laid end to end in the ROM come back as one run.

    A run is a ROM page fill, not a phrase: the packer put whatever
    fit into one 32 KB page, so the blocks in a run are unrelated.
    """
    rom = _blank_rom()
    first = Block(index=0, kind="cvsd", bank=0x7C, start=0x5000, end=0x5100,
                  chip="u18", offset=0x061000, size=0x100)
    second = first._replace(index=1, start=0x5100, end=0x5180,
                            offset=0x061100, size=0x80)
    apart = first._replace(index=2, start=0x6000, end=0x6040,
                           offset=0x062000, size=0x40)

    assert rom.runs([first, second, apart]) == [[first, second], [apart]]


def test_a_run_keeps_blocks_in_rom_order():
    rom = _blank_rom()
    a = Block(index=5, kind="cvsd", bank=0x7C, start=0x5000, end=0x5100,
              chip="u18", offset=0x061000, size=0x100)
    b = a._replace(index=1, start=0x5100, end=0x5180, offset=0x061100, size=0x80)

    # Given out of order, and with the lower index second.
    assert [x.index for x in rom.runs([b, a])[0]] == [5, 1]


def test_empty_blocks_are_left_out_of_runs():
    rom = _blank_rom()
    real = Block(index=0, kind="cvsd", bank=0x7C, start=0x5000, end=0x5100,
                 chip="u18", offset=0x061000, size=0x100)
    hollow = real._replace(index=1, start=0x5100, end=0x5100,
                           offset=0x061100, size=0)

    assert rom.runs([real, hollow]) == [[real]]


# -- the power-on checksum ---------------------------------------------


def _checked_rom():
    """A U18 whose self-test list names one page, which passes."""
    from wpcaudio.soundrom import SoundRom
    u18 = bytearray(0x80000)
    u18[0x7C000 + 0xDD00 - 0xC000:][:0] = b""
    list_at = 0x7C000 + 0xDD00 - 0xC000
    u18[list_at:list_at + 2] = bytes([0x70, 0xFF])
    page = 0                                  # bank $70 is U18 page 0
    u18[page + 0x10] = 0x33
    # Make the negated sum equal $70 through the correction byte.
    u18[page + 0x7FFF] = (-0x70 - 0x33) & 0xFF
    return SoundRom(u18=bytes(u18))


def test_the_self_test_list_is_read_from_the_fixed_bank():
    assert _checked_rom().checksum_banks() == [0x70]


def test_an_untouched_page_passes():
    assert _checked_rom().bad_checksums() == []


def test_an_edited_page_fails_until_fixed():
    rom = _checked_rom()
    rom.chips["u18"][0x100] = 0x5A

    assert rom.bad_checksums() == [0x70]
    assert rom.fix_checksums() == [0x70]
    assert rom.bad_checksums() == []


def test_fixing_touches_only_the_correction_byte():
    rom = _checked_rom()
    rom.chips["u18"][0x100] = 0x5A
    before = bytes(rom.chips["u18"])

    rom.fix_checksums()

    changed = [i for i, (a, b) in enumerate(zip(before, rom.chips["u18"])) if a != b]
    assert changed == [0x7FFF]


def test_an_image_without_a_list_reports_none():
    from wpcaudio.soundrom import SoundRom
    assert SoundRom(u18=bytes(0x80000)).checksum_banks() == []


def test_the_fixed_bank_page_is_corrected_below_the_fixed_bank():
    # U18's top page holds the fixed bank; its $BFFF is the reset vector.
    from wpcaudio.soundrom import SoundRom
    u18 = bytearray(0x80000)
    list_at = 0x7C000 + 0xDD00 - 0xC000
    u18[list_at:list_at + 2] = bytes([0x7F, 0xFF])
    u18[0x7FFFE:0x80000] = bytes([0xDD, 0xDD])
    rom = SoundRom(u18=bytes(u18))

    assert rom.correction_byte(0x7F) == ("u18", 0x7BFFF)
    rom.fix_checksums()
    assert rom.chips["u18"][0x7FFFE:] == bytes([0xDD, 0xDD])
    assert rom.bad_checksums() == []
