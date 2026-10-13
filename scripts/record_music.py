"""Runs a sound command on the emulated board and records the music.

    python scripts/record_music.py <romdir> --command 0x50 --seconds 8 -o out/musica

On a pre-DCS board the music is not recorded audio: it is a stream of
register writes into the YM2151. So there is no WAV to extract. What
this does instead is run U18's own 6809 program, send it a command the
way the game's CPU would, and log every register write it makes. The
log goes out as a **VGM** file, which any chiptune player renders --
and that sidesteps writing an FM synthesiser entirely.

It also reports what else the command did: which sample blocks it
started, and the FIRQ rate the program asked the YM2151 timer for,
which is the board's real sample clock.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wpcaudio.board import CPU_CLOCK, YM_CLOCK, SoundBoard
from wpcaudio.vgm import write_vgm

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sound_map import load_rom


def number(text):
    return int(text, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roms", help="directory holding the U14/U15/U18 images")
    parser.add_argument("--command", type=number, action="append", required=True,
                        metavar="BYTE", help="command to send; may be repeated")
    parser.add_argument("--seconds", type=float, default=8.0,
                        help="how long to run after the last command")
    parser.add_argument("--settle", type=float, default=0.05,
                        help="seconds to run between commands")
    parser.add_argument("-o", "--output", default="out/musica",
                        help="where the .vgm files go")
    arguments = parser.parse_args()

    rom, _ = load_rom(arguments.roms)
    board = SoundBoard(rom)
    board.boot()
    board.run(0.05)

    out = pathlib.Path(arguments.output)
    out.mkdir(parents=True, exist_ok=True)

    first = len(board.ym.writes)
    for command in arguments.command:
        board.send(command)
        board.run(arguments.settle)
    board.run(arguments.seconds)

    # The timer acknowledgement in the interrupt handler writes register
    # $14 thousands of times a second and carries no music; a player
    # would only stutter on it.
    log = [w for w in board.ym.writes[first:] if w.register != 0x14]
    if not log:
        print("el comando no escribio ningun registro de musica")
        return

    start = log[0].cycle
    log = [w._replace(cycle=w.cycle - start) for w in log]

    name = "_".join("%02X" % c for c in arguments.command)
    path = write_vgm(out / ("cmd_%s.vgm" % name), log,
                     cpu_clock=CPU_CLOCK, ym_clock=YM_CLOCK)

    print("%s: %d escrituras, %.1f s" % (path, len(log), log[-1].cycle / CPU_CLOCK))
    print("reloj de muestreo del FIRQ: %.0f Hz  ->  DAC %.0f Hz, bits CVSD %.0f Hz"
          % (board.firq_rate, 2 * board.firq_rate, 4 * board.firq_rate))

    if board.samples:
        print("muestras que el comando arranco:")
        for sample in board.samples:
            print("   %6.2f s  %-4s  banco $%02X  $%04X-$%04X"
                  % (sample.cycle / CPU_CLOCK, sample.stream,
                     sample.bank, sample.start, sample.end))


if __name__ == "__main__":
    main()
