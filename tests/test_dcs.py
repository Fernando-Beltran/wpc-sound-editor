"""Spec for reading DCS Explorer's listings."""

import io
import zipfile

from wpcaudio import dcs

TRACKS = """
----- Track index -----
Command  Ch  Type       Time               Address
00 00    0   1 Play     7.68ms             003D82 [U2 03D82]
00 01    0   1 Play     19545.60ms(loop)   003D93 [U2 03D93]
00 1a    3   1 Play     5191.68ms          004541 [U2 04541]
04 63    2   1 Play     1582.08ms          008245 [U2 08245]
"""

PROGRAMS = """
Track $000f Channel 0 {    // Address $000422b [U2 $0422b], Time 5191.68ms
    SetMixingLevel(level 110);                                      // 0000 07 00 6E
    Play(stream $14031A);                                           // 0000 01 00 14031A 01
    Wait(676) End;                                                  // 02A4 00
};
Track $001a Channel 3 {    // Address $0004541 [U2 $04541], Time 5191.68ms
    Play(stream $14031A);                                           // 0000 01 03 14031A 01
    Play(stream $00826E);
    Play(stream $14031A);
};
"""

STREAMS = """
Address             Fmt Stream Header                                    Time (sec)   Bytes Compressed  Uncompressed  Ratio
000826e [U2 0826e]  1   94 94 90 90 90 90 90 90 d0 d0 ff ff ff ff ff ff      2.62             16258        163680     10.1:1 (90.1%)
014031a [U3 4031a]  1   98 94 94 94 94 94 d4 d4 ff ff ff ff ff ff ff ff      5.19             45025        324480     7.2:1 (86.1%)
"""


def test_the_track_index_is_read_with_commands_as_numbers():
    tracks = dcs.parse_tracks(TRACKS)

    assert [t.command for t in tracks] == [0x0000, 0x0001, 0x001A, 0x0463]
    assert tracks[1].loop and not tracks[0].loop
    assert tracks[2].channel == 3 and tracks[2].ms == 5191.68


def test_each_program_lists_its_streams_once_in_order():
    plays = dcs.parse_programs(PROGRAMS)

    assert plays[0x000F] == [0x14031A]
    assert plays[0x001A] == [0x14031A, 0x00826E]


def test_streams_carry_their_length_and_size():
    streams = dcs.parse_streams(STREAMS)

    assert streams[0x14031A].seconds == 5.19
    assert streams[0x00826E].size == 16258


def test_a_command_is_shown_as_two_bytes():
    assert dcs.label(0x001A) == "00 1A"


def test_images_are_zipped_under_the_romset_names():
    data = dcs.zip_images({"u2": b"abc", "u3": b"de"}, {"u2": "ng_u2_s.l1", "u3": "ng_u3_s.l1"})

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert sorted(archive.namelist()) == ["ng_u2_s.l1", "ng_u3_s.l1"]
        assert archive.read("ng_u2_s.l1") == b"abc"


NARRATION = """
Track $0176 Channel 1 {    // Address $00060ee [U2 $060ee], Time 15275.52ms
    SetMixingLevel(level 105);                                      // 0000 07 01 69
    SetMixingLevel(channel 0, decrease 20);                         // 0000 09 00 14
    Play(stream $24D20A);                                           // 0000 01 01 24D20A 01
    Wait(172) Play(stream $33F558);                                 // 00AC 01 01 33F558 01
    Wait(370) SetMixingLevel(channel 0, increase 20, steps 15);     // 0172 0B 00 14 000F
    Wait(15) End;                                                   // 000F 00
};
"""


def test_a_program_body_keeps_its_statements_without_comments():
    channel, statements = dcs.parse_bodies(NARRATION)[0x0176]

    assert channel == 1
    assert statements[0] == "SetMixingLevel(level 105);"
    assert statements[-1] == "Wait(15) End;"


def test_a_new_track_program_keeps_the_set_up_and_the_close():
    channel, statements = dcs.parse_bodies(NARRATION)[0x0176]

    program = dcs.track_program(0x0176, channel, statements, "nueva.wav")

    assert program.splitlines() == [
        "Track $0176 channel 1 {",
        "    SetMixingLevel(level 105);",
        "    SetMixingLevel(channel 0, decrease 20);",
        '    Play(Stream "nueva.wav");',
        "    Wait(stream);",
        "    SetMixingLevel(channel 0, increase 20, steps 15);",
        "    Wait(15) End;",
        "};",
    ]


def test_a_stream_address_names_its_chip_and_offset():
    assert dcs.stream_place(0x14031A) == ("u3", 0x4031A)
    assert dcs.stream_place(0x00826E) == ("u2", 0x0826E)
    assert dcs.stream_place(0x67CFFE) == ("u8", 0x7CFFE)
