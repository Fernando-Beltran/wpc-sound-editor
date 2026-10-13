"""Spec for writing the YM2151 register log out as a VGM file."""

import struct

import pytest

from wpcaudio.board import RegisterWrite
from wpcaudio.vgm import VGM_SAMPLE_RATE, write_vgm


def _fields(data):
    return {
        "magic": data[0:4],
        "eof": struct.unpack_from("<I", data, 0x04)[0],
        "version": struct.unpack_from("<I", data, 0x08)[0],
        "total": struct.unpack_from("<I", data, 0x18)[0],
        "ym2151": struct.unpack_from("<I", data, 0x30)[0],
        "data_offset": struct.unpack_from("<I", data, 0x34)[0],
    }


def _body(data):
    return data[0x34 + struct.unpack_from("<I", data, 0x34)[0]:]


def test_the_header_identifies_a_ym2151_log(tmp_path):
    path = write_vgm(tmp_path / "x.vgm", [], cpu_clock=2000000, ym_clock=3579545)
    data = path.read_bytes()
    fields = _fields(data)

    assert fields["magic"] == b"Vgm "
    assert fields["version"] == 0x151
    assert fields["ym2151"] == 3579545
    # Both offsets are counted from their own field, as the format says.
    assert fields["eof"] + 0x04 == len(data)
    assert fields["data_offset"] + 0x34 == 0x100


def test_a_register_write_becomes_a_ym2151_command(tmp_path):
    writes = [RegisterWrite(cycle=0, register=0x28, value=0x4A)]
    path = write_vgm(tmp_path / "x.vgm", writes, cpu_clock=2000000, ym_clock=3579545)

    assert _body(path.read_bytes()).startswith(b"\x54\x28\x4a")


def test_the_log_ends_with_the_end_marker(tmp_path):
    path = write_vgm(tmp_path / "x.vgm", [], cpu_clock=2000000, ym_clock=3579545)
    assert _body(path.read_bytes()).endswith(b"\x66")


def test_time_between_writes_becomes_a_wait(tmp_path):
    # Half a second of CPU cycles is half a second of VGM samples.
    writes = [RegisterWrite(0, 0x20, 0x01), RegisterWrite(1000000, 0x20, 0x02)]
    path = write_vgm(tmp_path / "x.vgm", writes, cpu_clock=2000000, ym_clock=3579545)
    body = _body(path.read_bytes())

    assert body.startswith(b"\x54\x20\x01")
    # A wait longer than one command's 16-bit limit is split across several.
    waits = []
    index = 3
    while body[index] == 0x61:
        waits.append(struct.unpack_from("<H", body, index + 1)[0])
        index += 3
    assert sum(waits) == VGM_SAMPLE_RATE // 2
    assert body[index:index + 3] == b"\x54\x20\x02"


def test_the_total_sample_count_covers_the_whole_log(tmp_path):
    writes = [RegisterWrite(0, 0x20, 0x01), RegisterWrite(2000000, 0x20, 0x02)]
    path = write_vgm(tmp_path / "x.vgm", writes, cpu_clock=2000000,
                     ym_clock=3579545, tail_samples=0)

    assert _fields(path.read_bytes())["total"] == VGM_SAMPLE_RATE


def test_a_tail_of_silence_is_added_so_the_last_note_is_not_cut(tmp_path):
    writes = [RegisterWrite(0, 0x20, 0x01), RegisterWrite(2000000, 0x20, 0x02)]
    path = write_vgm(tmp_path / "x.vgm", writes, cpu_clock=2000000,
                     ym_clock=3579545, tail_samples=1000)

    assert _fields(path.read_bytes())["total"] == VGM_SAMPLE_RATE + 1000


def test_writes_must_arrive_in_order(tmp_path):
    writes = [RegisterWrite(1000, 0x20, 0x01), RegisterWrite(0, 0x20, 0x02)]
    with pytest.raises(ValueError):
        write_vgm(tmp_path / "x.vgm", writes, cpu_clock=2000000, ym_clock=3579545)
