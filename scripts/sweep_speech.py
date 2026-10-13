"""Maps the speech table by sweeping a command's parameter.

    python scripts/sweep_speech.py <romdir> -o mapas/voz_tz_l2.json

Sending every command byte reaches only six of the 166 CVSD blocks,
and the reason is not that speech is hard to trigger: **all six
speech-starting commands are kind 7, and kind 7 has exactly six
commands.** The handler takes the command's parameter and indexes its
own table at `$400B`; the commands only expose six of its entries, and
whatever plays the rest does so from inside a script.

So this goes round the front door. It patches the parameter byte of
one kind 7 command in the command table, sends that command, and
watches what the board starts -- stepping the parameter through every
value. The handler is the real one, the table is the real one; only
the index is ours.
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wpcaudio.board import SoundBoard
from wpcaudio.soundrom import SoundRom

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sound_map import load_rom

DIRECTORY_PAGE = 0x060000
COMMAND_TABLE_POINTER = 0x400F


def directory(image, address, length=1):
    offset = DIRECTORY_PAGE + address - 0x4000
    return image[offset:offset + length]


def parameter_offset(image, command):
    """Where that command's parameter byte sits in the U18 image."""
    pair = directory(image, COMMAND_TABLE_POINTER, 2)
    table = (pair[0] << 8) | pair[1]
    return DIRECTORY_PAGE + (table - 0x4000) + command * 2 + 1


def speech_commands(image):
    """The kind 7 commands, which are the ones that start speech."""
    pair = directory(image, COMMAND_TABLE_POINTER, 2)
    table = (pair[0] << 8) | pair[1]
    found = []
    for command in range(256):
        offset = DIRECTORY_PAGE + (table - 0x4000) + command * 2
        if image[offset] == 7:
            found.append((command, image[offset + 1]))
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roms")
    parser.add_argument("--command", type=lambda t: int(t, 0),
                        help="which kind 7 command to borrow (default: the first)")
    parser.add_argument("--seconds", type=float, default=2.5)
    parser.add_argument("--first", type=lambda t: int(t, 0), default=0)
    parser.add_argument("--last", type=lambda t: int(t, 0), default=0xFF)
    parser.add_argument("-o", "--output", default="mapas/voz.json")
    arguments = parser.parse_args()

    original, images = load_rom(arguments.roms)
    index = {}
    for block in original.cvsd_blocks():
        index[("cvsd", block.bank, block.start, block.end)] = block.index
    for block in original.dac_blocks():
        index[("dac", block.bank, block.start, block.end)] = block.index

    speech = speech_commands(original.chips["u18"])
    if not speech:
        raise SystemExit("este juego no tiene comandos de tipo 7")
    command = arguments.command if arguments.command is not None else speech[0][0]
    offset = parameter_offset(original.chips["u18"], command)

    print("comandos de voz (tipo 7): %s"
          % ", ".join("$%02X(p$%02X)" % pair for pair in speech))
    print("tomando prestado $%02X; su parametro vive en U18 0x%06X\n"
          % (command, offset))

    rows = []
    for parameter in range(arguments.first, arguments.last + 1):
        rom = SoundRom(**images)
        rom.chips["u18"][offset] = parameter
        board = SoundBoard(rom)
        try:
            board.boot()
            board.run(0.05)
            before = len(board.samples)
            board.send(command)
            board.run(arguments.seconds)
        except Exception as problem:                      # noqa: BLE001
            rows.append({"param": parameter, "error": str(problem)})
            print("  $%02X  error: %s" % (parameter, problem), flush=True)
            continue

        order, unknown = [], 0
        for start in board.samples[before:]:
            found = index.get((start.stream, start.bank, start.start, start.end))
            if found is None:
                unknown += 1
                continue
            label = "%s:%d" % (start.stream, found)
            if label not in order:
                order.append(label)
        rows.append({"param": parameter, "samples": order, "unknown": unknown})
        print("  $%02X  %s%s" % (parameter, ", ".join(order) or "nada",
                                 "  (?%d)" % unknown if unknown else ""),
              flush=True)

    out = pathlib.Path(arguments.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"command": command, "entries": rows}, indent=1),
                   encoding="utf-8")

    reached = {label for row in rows for label in row.get("samples", [])}
    print("\n%s: %d entradas, %d bloques distintos alcanzados"
          % (out, len(rows), len(reached)))


if __name__ == "__main__":
    main()
