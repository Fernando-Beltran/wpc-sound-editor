"""Spec for the editor core: load, map, listen, replace, download."""

import io
import json
import zipfile

import numpy as np
import pytest

from wpcaudio import games
from wpcaudio.wavio import read_wav, write_wav
from wpcide.app import CATEGORIES, Ide, NotLoaded


def _images():
    """A miniature ROM set carrying one DAC block and one CVSD block."""
    u18 = bytearray(0x80000)
    u15 = bytearray(0x80000)

    def put(bank, address, payload):
        offset = ((bank & 0x0F) << 15) + (address - 0x4000)
        image = u18 if (bank & 0xE0) == 0x60 else u15
        image[offset:offset + len(payload)] = payload

    put(0x7C, 0x4003, bytes([0x50, 0x00]))             # DAC table ->
    put(0x7C, 0x5000, bytes([0x7C, 0x60, 0x00, 0x68, 0x00]))
    put(0x7C, 0x5005, bytes(5))
    put(0x7C, 0x6000, bytes(0x80 for _ in range(0x800)))

    put(0x7C, 0x4015, bytes([0x51, 0x00]))             # CVSD pointers ->
    put(0x7C, 0x5100, bytes([0x52, 0x00]))
    put(0x7C, 0x5102, bytes(2))
    put(0x7C, 0x5200, bytes([0xA0, 0x40, 0x00, 0x44, 0x00]))
    put(0xA0, 0x4000, bytes(0x55 for _ in range(0x400)))

    # Command $20: kind 4, two voice programs of FM.
    put(0x7C, 0x400F, bytes([0x53, 0x00]))             # command table ->
    put(0x7C, 0x5300 + 0x20 * 2, bytes([4, 0]))
    put(0x7C, 0x4007, bytes([0x54, 0x00]))             # kind 4 table ->
    put(0x7C, 0x5400, bytes([0x55, 0x00]))
    put(0x7C, 0x5500, bytes([0x03, 0x56, 0x00, 0x57, 0x00]))
    put(0x7C, 0x5600, bytes([0x30] * 16))
    put(0x7C, 0x5700, bytes([0x31] * 16))
    return {"u18": bytes(u18), "u15": bytes(u15)}


@pytest.fixture
def ide(monkeypatch, tmp_path):
    images = _images()
    monkeypatch.setitem(games.GAMES, "prueba", {
        "name": "Juego de prueba",
        "ipdb": 2684,
        "roms": {socket: games.crc32(data) for socket, data in images.items()},
        "sizes": {socket: len(data) for socket, data in images.items()},
        "directory": {"directory_bank": 0x7C, "dac_table": 0x4003,
                      "cvsd_table": 0x4015},
    })
    # Notes persist to disk, so each test gets its own corner of it.
    editor = Ide(notes_dir=tmp_path / "notas")
    for socket, data in images.items():
        editor.add_image(socket, data)
    editor.load()
    return editor


def _wav(seconds, rate, freq=300):
    t = np.arange(int(seconds * rate)) / rate
    samples = (8000 * np.sin(2 * np.pi * freq * t)).astype(np.int16)
    buffer = io.BytesIO()
    write_wav(buffer, samples, rate)
    return buffer.getvalue()


# -- loading ---------------------------------------------------------


def test_an_unknown_set_does_not_load():
    editor = Ide()
    editor.add_image("u18", bytes(0x80000))
    assert editor.load() is None


def test_asking_for_the_map_before_loading_is_refused():
    with pytest.raises(NotLoaded):
        Ide().chip_map()


def test_a_matching_set_identifies_and_reports_every_socket(ide):
    assert ide.game.key == "prueba"
    assert all(report.ok for report in ide.report().values())


# -- the map ---------------------------------------------------------


def test_the_map_places_each_block_on_its_chip(ide):
    chips = {chip["name"]: chip for chip in ide.chip_map()["chips"]}

    assert chips["u18"]["pages"] == 16
    dac = chips["u18"]["blocks"][0]
    assert (dac["kind"], dac["index"], dac["size"]) == ("dac", 0, 0x800)
    cvsd = chips["u15"]["blocks"][0]
    assert (cvsd["kind"], cvsd["size"]) == ("cvsd", 0x400)


def test_the_map_carries_the_measured_rates(ide):
    rates = ide.chip_map()["rates"]
    # Timer A's 640-clock period, two DAC samples and four CVSD bits per
    # interrupt. Not a guess any more.
    assert round(rates["firq"]) == 5593
    assert round(rates["dac"]) == 11186
    assert round(rates["cvsd"]) == 22372


def test_a_block_comes_back_as_a_playable_wav(ide):
    data = ide.block_wav("dac", 0)
    samples, rate = read_wav(io.BytesIO(data))

    assert data[:4] == b"RIFF"
    assert len(samples) == 0x800
    assert round(rate) == 11186


# -- editing ---------------------------------------------------------


def test_replacing_a_block_reports_what_it_cost(ide):
    # Twice as much audio as the block can hold.
    report = ide.replace("dac", 0, _wav(2 * 0x800 / 11186.0, 11186))

    assert report["truncated"] > 0
    assert report["padded"] == 0
    assert report["block"]["edited"] is True


def test_a_short_replacement_is_padded_not_stretched(ide):
    report = ide.replace("dac", 0, _wav(0x100 / 11186.0, 11186))

    assert report["padded"] > 0
    assert report["truncated"] == 0


def test_an_edit_shows_up_on_the_map(ide):
    ide.replace("cvsd", 0, _wav(0.01, 22373))
    assert ide.chip_map()["edits"] == ["cvsd:0"]


def test_reverting_puts_the_original_bytes_back(ide):
    before = ide.block_wav("dac", 0)
    ide.replace("dac", 0, _wav(0.05, 11186))
    assert ide.block_wav("dac", 0) != before

    ide.revert("dac", 0)
    assert ide.block_wav("dac", 0) == before
    assert ide.chip_map()["edits"] == []


def test_editing_an_unknown_block_is_refused(ide):
    with pytest.raises(KeyError):
        ide.replace("dac", 99, _wav(0.01, 11186))


# -- getting it back out ---------------------------------------------


def test_the_download_holds_every_image_at_its_original_size(ide):
    ide.replace("dac", 0, _wav(0.05, 11186))

    with zipfile.ZipFile(io.BytesIO(ide.patched())) as archive:
        names = sorted(archive.namelist())
        assert names == ["README.txt", "prueba_u15.rom", "prueba_u18.rom"]
        assert len(archive.read("prueba_u18.rom")) == 0x80000
        assert "dac:0" in archive.read("README.txt").decode()


def test_an_untouched_set_downloads_byte_identical(ide):
    with zipfile.ZipFile(io.BytesIO(ide.patched())) as archive:
        assert archive.read("prueba_u18.rom") == ide.images["u18"]
        assert "Sin cambios" in archive.read("README.txt").decode()


# -- choosing which part of the audio goes in -------------------------


def test_staging_reports_the_slider_range_without_writing(ide):
    before = ide.block_wav("dac", 0)

    report = ide.stage("dac", 0, _wav(2 * 0x800 / 11186.0, 11186))

    assert report["longer"] is True
    assert report["max_offset"] > 0
    assert ide.block_wav("dac", 0) == before, "staging no debe escribir nada"
    assert ide.chip_map()["edits"] == []


def test_a_preview_is_the_codec_result_not_the_original_file(ide):
    ide.stage("dac", 0, _wav(2 * 0x800 / 11186.0, 11186))

    early = ide.preview("dac", 0, offset=0)
    late = ide.preview("dac", 0, offset=0x800)

    assert early[:4] == b"RIFF"
    assert early != late, "el deslizador no mueve la ventana"
    assert ide.block_wav("dac", 0) != early, "la vista previa no debe escribir"


def test_committing_at_an_offset_writes_that_window(ide):
    ide.stage("dac", 0, _wav(2 * 0x800 / 11186.0, 11186))
    chosen = ide.preview("dac", 0, offset=0x400)

    ide.commit("dac", 0, offset=0x400)

    assert ide.block_wav("dac", 0) == chosen
    assert ide.chip_map()["edits"] == ["dac:0"]


def test_a_short_wav_can_be_placed_later_in_the_block(ide):
    report = ide.stage("dac", 0, _wav(0x200 / 11186.0, 11186))

    assert report["longer"] is False
    assert report["max_offset"] > 0
    assert report["padded"] > 0


# -- the user's own notes --------------------------------------------


def test_a_block_can_be_annotated_and_coloured(ide):
    ide.annotate("dac", 0, note="Disparo de la nave", colour="#d04f4f")

    block = ide.chip_map()["chips"][0]["blocks"][0]
    assert block["note"] == "Disparo de la nave"
    assert block["colour"] == "#d04f4f"


def test_annotations_survive_being_saved_and_reloaded(ide):
    ide.annotate("cvsd", 0, note="Voz del narrador")

    again = Ide(notes_dir=ide.notes_dir)
    for socket, data in ide.images.items():
        again.add_image(socket, data)
    again.load()

    assert again.chip_map()["chips"][1]["blocks"][0]["note"] == "Voz del narrador"


def test_clearing_an_annotation_removes_it(ide):
    ide.annotate("dac", 0, note="algo", colour="#fff")
    ide.annotate("dac", 0, note="", colour="")

    block = ide.chip_map()["chips"][0]["blocks"][0]
    assert block["note"] == "" and block["colour"] is None


def test_notes_travel_with_the_download(ide):
    ide.annotate("dac", 0, note="Disparo")

    with zipfile.ZipFile(io.BytesIO(ide.patched())) as archive:
        notes = json.loads(archive.read("notas.json"))
    assert notes["dac:0"]["note"] == "Disparo"


def test_blocks_can_be_marked_for_editing(ide):
    ide.annotate("dac", 0, marked=True)
    ide.annotate("cvsd", 0, marked=True)
    ide.annotate("cvsd", 0, marked=False)

    assert ide.chip_map()["marked"] == ["dac:0"]
    assert ide.chip_map()["chips"][0]["blocks"][0]["marked"] is True


def test_every_sound_can_be_downloaded_at_once(ide):
    with zipfile.ZipFile(io.BytesIO(ide.wav_bundle())) as archive:
        names = sorted(n for n in archive.namelist() if n.endswith(".wav"))
    assert names == ["cvsd_000.wav", "dac_000.wav"]


def test_only_the_marked_ones_can_be_downloaded(ide):
    ide.annotate("cvsd", 0, marked=True)

    with zipfile.ZipFile(io.BytesIO(ide.wav_bundle(only_marked=True))) as archive:
        names = sorted(n for n in archive.namelist() if n.endswith(".wav"))
    assert names == ["cvsd_000.wav"]


def test_a_bundle_names_files_after_the_note_when_there_is_one(ide):
    ide.annotate("dac", 0, note="Disparo de la nave")

    with zipfile.ZipFile(io.BytesIO(ide.wav_bundle())) as archive:
        assert "dac_000_Disparo_de_la_nave.wav" in archive.namelist()


def test_asking_for_marked_ones_when_none_are_marked_gives_an_empty_bundle(ide):
    with zipfile.ZipFile(io.BytesIO(ide.wav_bundle(only_marked=True))) as archive:
        assert [n for n in archive.namelist() if n.endswith(".wav")] == []


def test_a_category_gives_a_block_its_colour(ide):
    ide.annotate("cvsd", 0, category="voice")

    block = ide.chip_map()["chips"][1]["blocks"][0]
    assert block["category"] == "voice"
    assert block["colour"] == CATEGORIES["voice"]


def test_a_custom_colour_overrides_the_category(ide):
    ide.annotate("cvsd", 0, category="voice", colour="#ff00ff")

    block = ide.chip_map()["chips"][1]["blocks"][0]
    assert block["category"] == "voice"
    assert block["colour"] == "#ff00ff"


def test_the_known_categories_travel_with_the_map(ide):
    assert set(ide.chip_map()["categories"]) >= {"voice", "effect", "music"}


def test_an_unknown_category_is_refused(ide):
    with pytest.raises(ValueError):
        ide.annotate("cvsd", 0, category="no-existe")


# -- what triggers each sound ----------------------------------------


def test_a_block_shows_which_commands_play_it(ide, tmp_path):
    (tmp_path / "mapas").mkdir()
    (tmp_path / "mapas" / "prueba.json").write_text(json.dumps([
        {"cmd": 0x06, "keyon": 3, "writes": 9, "samples": ["dac:0"], "unknown": 0},
        {"cmd": 0x11, "keyon": 0, "writes": 0, "samples": ["dac:0", "cvsd:0"],
         "unknown": 0},
    ]), encoding="utf-8")
    ide.maps_dir = tmp_path / "mapas"
    ide.load()

    chips = {chip["name"]: chip for chip in ide.chip_map()["chips"]}
    assert chips["u18"]["blocks"][0]["commands"] == [0x06, 0x11]
    assert chips["u15"]["blocks"][0]["commands"] == [0x11]


def test_the_map_says_how_much_of_the_rom_the_scan_reached(ide, tmp_path):
    (tmp_path / "mapas").mkdir()
    (tmp_path / "mapas" / "prueba.json").write_text(json.dumps([
        {"cmd": 1, "keyon": 0, "writes": 0, "samples": ["dac:0"], "unknown": 0},
    ]), encoding="utf-8")
    ide.maps_dir = tmp_path / "mapas"
    ide.load()

    coverage = ide.chip_map()["coverage"]
    assert coverage["reached"] == 1
    assert coverage["total"] == 2


def test_without_a_scan_the_editor_still_works(ide):
    assert ide.chip_map()["coverage"]["reached"] == 0
    assert ide.chip_map()["chips"][0]["blocks"][0]["commands"] == []


def test_every_block_carries_its_silhouette(ide):
    block = ide.chip_map()["chips"][0]["blocks"][0]

    assert len(block["shape"]) == 48
    assert all(0 <= value <= 15 for value in block["shape"])


def test_an_edit_refreshes_the_silhouette(ide):
    before = ide.chip_map()["chips"][0]["blocks"][0]["shape"]
    ide.replace("dac", 0, _wav(0x800 / 11186.0, 11186))

    assert ide.chip_map()["chips"][0]["blocks"][0]["shape"] != before


def test_notes_can_be_exported_and_brought_back(ide):
    ide.annotate("dac", 0, note="Disparo", category="effect", marked=True)
    document = ide.export_notes()

    fresh = Ide(notes_dir=ide.notes_dir / "otro")
    for socket, data in ide.images.items():
        fresh.add_image(socket, data)
    fresh.load()
    assert fresh.chip_map()["chips"][0]["blocks"][0]["note"] == ""

    fresh.import_notes(document)
    block = fresh.chip_map()["chips"][0]["blocks"][0]
    assert (block["note"], block["category"], block["marked"]) == ("Disparo", "effect", True)


def test_importing_merges_unless_told_to_replace(ide):
    ide.annotate("dac", 0, note="uno")
    ide.import_notes({"notes": {"cvsd:0": {"note": "dos"}}})
    assert ide.note_for("dac", 0)["note"] == "uno"

    ide.import_notes({"notes": {"cvsd:0": {"note": "tres"}}}, replace=True)
    assert ide.note_for("dac", 0) == {}
    assert ide.note_for("cvsd", 0)["note"] == "tres"


def test_importing_rubbish_is_refused(ide):
    with pytest.raises(ValueError):
        ide.import_notes({"cualquier": "cosa"})


def test_the_status_lists_the_three_sockets_even_when_empty():
    editor = Ide()

    status = editor.status()
    assert [s["socket"] for s in status["sockets"]] == ["u18", "u15", "u14"]
    assert all(s["loaded"] is False for s in status["sockets"])
    assert status["game"] is None


def test_a_loaded_socket_shows_its_size_and_checksum(ide):
    status = ide.status()
    u18 = next(s for s in status["sockets"] if s["socket"] == "u18")

    assert u18["loaded"] is True
    assert u18["size"] == 0x80000
    assert len(u18["crc"]) == 8
    assert u18["ok"] is True
    assert status["game"]["key"] == "prueba"


def test_a_socket_the_game_does_not_use_is_not_checked(ide):
    u14 = next(s for s in ide.status()["sockets"] if s["socket"] == "u14")

    assert u14["loaded"] is False and u14["ok"] is None


def test_the_original_can_still_be_heard_after_an_edit(ide):
    before = ide.block_wav("dac", 0)
    ide.replace("dac", 0, _wav(0.05, 11186))

    assert ide.block_wav("dac", 0) != before
    assert ide.block_wav("dac", 0, original=True) == before


def test_a_fit_can_be_costed_before_choosing_it(ide):
    ide.stage("dac", 0, _wav(0x200 / 11186.0, 11186))

    trimmed = ide.fit_preview("dac", 0, mode="trim")
    looped = ide.fit_preview("dac", 0, mode="loop")

    assert trimmed["padded"] > 0
    assert looped["padded"] == 0
    assert looped["mode"] == "loop"


def test_music_commands_can_be_annotated_too(ide):
    ide.annotate("music", 0x0E, note="Tema principal", category="music", marked=True)

    assert ide.note_for("music", 0x0E)["note"] == "Tema principal"
    # A music mark is not a block mark: the bundle of WAVs must not
    # try to extract it.
    assert ide.chip_map()["marked"] == []


def test_annotating_a_command_that_cannot_exist_is_refused(ide):
    with pytest.raises(KeyError):
        ide.annotate("music", 999, note="nada")


# -- music replaced by a recording -------------------------------------


def _program(ide, address, length):
    offset = 0x060000 + address - 0x4000
    return bytes(ide.rom.chips["u18"][offset:offset + length])


def test_staging_music_suggests_blocks_that_hold_it(ide):
    report = ide.stage_music(0x20, _wav(0.2, 22050), kind="cvsd")

    assert report["blocks"] == [0]
    assert report["kind"] == "music"


def test_committing_music_rewrites_its_programs(ide):
    ide.stage_music(0x20, _wav(0.2, 22050), kind="cvsd")

    ide.commit_music(0x20, [0], kind="cvsd")

    # The roomier program hosts the list; the other is silenced.
    assert _program(ide, 0x5700, 4) == b"\x00\x00\xff\x15"
    assert _program(ide, 0x5600, 1) == b"\x15"
    assert ("cvsd", 0) in ide.edits


def test_a_replaced_command_plays_its_recording(ide):
    ide.stage_music(0x20, _wav(0.2, 22050), kind="cvsd")
    ide.commit_music(0x20, [0], kind="cvsd")

    samples, rate = read_wav(io.BytesIO(ide.music_wav(0x20)))

    assert rate == int(ide.rate("cvsd"))
    assert np.abs(samples).max() > 1000


def test_reverting_music_restores_programs_and_blocks(ide):
    before = bytes(ide.rom.chips["u18"]), bytes(ide.rom.chips["u15"])
    ide.stage_music(0x20, _wav(0.2, 22050), kind="cvsd")
    ide.commit_music(0x20, [0], kind="cvsd")

    ide.revert_music(0x20)

    assert (bytes(ide.rom.chips["u18"]), bytes(ide.rom.chips["u15"])) == before
    assert not ide.edits


def test_the_music_preview_is_a_wav(ide):
    ide.stage_music(0x20, _wav(0.2, 22050), kind="cvsd")

    samples, rate = read_wav(io.BytesIO(ide.music_preview(0x20, [0], kind="cvsd")))

    assert len(samples) == 0x400 * 8


def test_a_command_sharing_the_programs_is_replaced_too(ide):
    ide.stage_music(0x20, _wav(0.2, 22050), kind="cvsd")
    ide.commit_music(0x20, [0], kind="cvsd")
    ide.music_edits[0x20]["shared"] = [0x21]

    assert ide._replaced_by(0x21) == 0x20
    assert ide.music_wav(0x21) == ide.music_wav(0x20)


def test_dac_is_the_default_chip_for_music(ide):
    report = ide.stage_music(0x20, _wav(0.1, 22050))

    assert report["chip"] == "dac"
    assert report["blocks"] == [0]


def test_a_dac_replacement_plays_each_block_for_its_length(ide):
    ide.stage_music(0x20, _wav(0.1, 22050))

    ide.commit_music(0x20, [0])

    # 0x800 bytes at 22 a tick is 94 ticks, $005E.
    assert _program(ide, 0x5700, 5) == bytes([0x88, 0, 0x00, 0x5E, 0x95])
    assert ("dac", 0) in ide.edits
    assert ide.music_commands is not None


def test_reverting_a_dac_replacement_restores_everything(ide):
    before = bytes(ide.rom.chips["u18"])
    ide.stage_music(0x20, _wav(0.1, 22050))
    ide.commit_music(0x20, [0])

    ide.revert_music(0x20)

    assert bytes(ide.rom.chips["u18"]) == before


def test_a_second_editor_on_a_taken_port_defers_to_the_first(monkeypatch):
    # The usual "it does nothing": the port is already held by an editor.
    import threading
    import wpcide.server as srv
    opened = []
    monkeypatch.setattr(srv.webbrowser, "open", lambda url: opened.append(url))
    first = srv.EditorServer(("127.0.0.1", 0), srv.Handler)
    first.ide = Ide()
    port = first.server_address[1]
    thread = threading.Thread(target=first.serve_forever, daemon=True)
    thread.start()
    try:
        assert srv.serve(port=port, open_browser=True, tries=1) is None
        assert opened == ["http://127.0.0.1:%d/" % port]
    finally:
        first.shutdown()
        first.server_close()


def test_a_port_held_by_something_else_moves_to_the_next(monkeypatch):
    import socket
    import threading
    import wpcide.server as srv
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    monkeypatch.setattr(srv, "_editor_at", lambda p: False)
    started = {}

    def fake_forever(self, *a, **k):
        started["port"] = self.server_address[1]
    monkeypatch.setattr(srv.EditorServer, "serve_forever", fake_forever)
    try:
        srv.serve(port=port, open_browser=False, tries=5)
        assert started["port"] != port
    finally:
        holder.close()


def test_dcs_sockets_are_accepted_one_image_at_a_time():
    from wpcide.server import KNOWN_SOCKETS
    assert {"u18", "u15", "u14", "u2", "u8"} <= KNOWN_SOCKETS


def test_a_missing_vgmplay_is_reported_with_its_download_link():
    import json as _json
    import threading
    import urllib.error
    import urllib.request
    import wpcide.server as srv
    from wpcaudio import vgmplay

    server = srv.EditorServer(("127.0.0.1", 0), srv.Handler)
    server.ide = Ide()

    def missing(*args, **kwargs):
        raise vgmplay.NotInstalled("falta VGMPlay")
    server.ide.music_wav = missing
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = "http://127.0.0.1:%d/api/music/14" % server.server_address[1]
        try:
            urllib.request.urlopen(url, timeout=5)
            raise AssertionError("expected an error")
        except urllib.error.HTTPError as error:
            body = _json.loads(error.read())
            assert error.code == 503
            assert body["code"] == "vgmplay_missing"
            assert body["url"].startswith("https://github.com/vgmrips/")
    finally:
        server.shutdown()
        server.server_close()
