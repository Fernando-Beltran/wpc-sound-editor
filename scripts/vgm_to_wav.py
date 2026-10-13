"""Renders a VGM register log to WAV with VGMPlay.

    python scripts/vgm_to_wav.py out/musica/cmd_0E.vgm

VGMPlay carries an accurate YM2151 core, so this is what turns the
register log recorded from the emulated sound board into audio without
this project having to implement FM synthesis. It is a separate
program, run as a subprocess, so nothing about its licence reaches our
code.

Rendering is driven by `LogSound` in `VGMPlay.ini`: 1 writes the WAV
without playing. The setting is restored afterwards so dropping a file
on `VGMPlay.exe` still plays it through the speakers.
"""

import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
VGMPLAY = ROOT / "tools" / "vgmplay" / "VGMPlay.exe"
CONFIG = ROOT / "tools" / "vgmplay" / "VGMPlay.ini"


def render(vgm, quiet=True):
    """Renders one .vgm beside itself as .wav and returns the path."""
    if not VGMPLAY.exists():
        raise SystemExit("falta VGMPlay en %s" % VGMPLAY)

    original = CONFIG.read_text(encoding="latin-1")
    CONFIG.write_text(re.sub(r"(?m)^LogSound\s*=.*$", "LogSound = 1", original),
                      encoding="latin-1")
    try:
        subprocess.run([str(VGMPLAY), str(pathlib.Path(vgm).resolve())],
                       cwd=str(VGMPLAY.parent), check=True,
                       stdout=subprocess.DEVNULL if quiet else None,
                       stderr=subprocess.DEVNULL if quiet else None)
    finally:
        CONFIG.write_text(original, encoding="latin-1")

    wav = pathlib.Path(vgm).with_suffix(".wav")
    if not wav.exists():
        raise SystemExit("VGMPlay no dejo ningun WAV junto a %s" % vgm)
    return wav


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vgm", nargs="+", help="one or more .vgm files")
    arguments = parser.parse_args()

    for name in arguments.vgm:
        for path in sorted(pathlib.Path().glob(name)) or [pathlib.Path(name)]:
            wav = render(path)
            print("%s -> %s (%.1f KB)" % (path, wav, wav.stat().st_size / 1024))


if __name__ == "__main__":
    main()
