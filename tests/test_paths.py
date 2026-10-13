"""Spec for telling bundled files from the user's own."""

import pathlib
import sys

from wpcide import paths


def test_running_from_source_reads_bundles_from_the_project():
    assert not paths.frozen()
    assert (paths.bundled("wpcide", "static", "index.html")).exists()


def test_a_frozen_bundle_reads_from_where_it_was_unpacked(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert paths.frozen()
    assert paths.bundled("wpcide", "static") == tmp_path / "wpcide" / "static"


def test_the_users_files_go_to_their_own_directory(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_ENV, str(tmp_path / "datos"))

    target = paths.user_data("notas", "tz_l2.json")

    assert target == tmp_path / "datos" / "notas" / "tz_l2.json"
    # The directory is there to be written into, the file is not made.
    assert target.parent.is_dir()
    assert not target.exists()


def test_the_users_directory_is_never_the_working_directory(monkeypatch):
    monkeypatch.delenv(paths.DATA_ENV, raising=False)

    target = paths.user_data("notas")

    assert target.is_absolute()
    assert target != pathlib.Path("notas").resolve()


def test_a_bundled_folder_is_copied_once_to_be_written_to(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"
    (bundle / "tools" / "vgmplay").mkdir(parents=True)
    (bundle / "tools" / "vgmplay" / "VGMPlay.ini").write_text("LogSound = 0",
                                                              encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv(paths.DATA_ENV, str(tmp_path / "datos"))

    first = paths.writable_copy("tools", "vgmplay")
    assert (first / "VGMPlay.ini").read_text(encoding="utf-8") == "LogSound = 0"

    # Edited by the program, the copy is not overwritten next time.
    (first / "VGMPlay.ini").write_text("LogSound = 1", encoding="utf-8")
    again = paths.writable_copy("tools", "vgmplay")
    assert again == first
    assert (again / "VGMPlay.ini").read_text(encoding="utf-8") == "LogSound = 1"
