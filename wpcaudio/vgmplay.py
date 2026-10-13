"""Rendering a VGM register log to audio with VGMPlay.

VGMPlay carries an accurate YM2151 core, so this project never had to
write an FM synthesiser -- which is the one piece of the pre-DCS board
that would have been a serious undertaking on its own. It runs as a
separate program, so its licence stays its own.

Rendering is driven by `LogSound` in `VGMPlay.ini`: 1 writes a WAV
instead of playing. The setting is put back afterwards, so dropping a
file on the executable still plays through the speakers.
"""

import pathlib
import re
import subprocess


def _folder():
    """Where VGMPlay runs from.

    It rewrites its own `.ini` to switch between playing and
    logging, so it cannot live in a bundle directory: read-only at
    best, deleted on exit in the one-file case. Packaged, it is
    copied once to the user's data directory and run from there.
    """
    try:
        from wpcide import paths
        if paths.frozen():
            return paths.writable_copy("tools", "vgmplay")
        return paths.bundled("tools", "vgmplay")
    except Exception:          # noqa: BLE001
        return pathlib.Path(__file__).resolve().parent.parent / "tools" / "vgmplay"


VGMPLAY = _folder() / "VGMPlay.exe"
CONFIG = VGMPLAY.parent / "VGMPlay.ini"


class NotInstalled(Exception):
    """VGMPlay is not where this expects it."""


def available():
    return VGMPLAY.exists() and CONFIG.exists()


def render(vgm_path, timeout=120):
    """Renders one .vgm beside itself as .wav and returns that path."""
    if not available():
        raise NotInstalled("falta VGMPlay en %s" % VGMPLAY)

    vgm_path = pathlib.Path(vgm_path).resolve()
    original = CONFIG.read_text(encoding="latin-1")
    CONFIG.write_text(re.sub(r"(?m)^LogSound\s*=.*$", "LogSound = 1", original),
                      encoding="latin-1")
    try:
        subprocess.run([str(VGMPLAY), str(vgm_path)],
                       cwd=str(VGMPLAY.parent), check=True, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        CONFIG.write_text(original, encoding="latin-1")

    wav = vgm_path.with_suffix(".wav")
    if not wav.exists():
        raise NotInstalled("VGMPlay no dejo ningun WAV junto a %s" % vgm_path)
    return wav
