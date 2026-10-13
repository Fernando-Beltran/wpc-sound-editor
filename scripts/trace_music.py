"""Traces the sound-script VM as it runs, voice by voice.

    python scripts/trace_music.py <romdir> --command 0x0E --seconds 3 --voice 0

The main loop dispatches every opcode through one instruction,
`$DF71: JSR [A,X]`, with U pointing at the voice, `$04,U` holding the
opcode byte and `$06,U` the script pointer just past it. Hooking that
one address gives every opcode every voice executes, in order, with the
script clock (`$36`) at the time -- and the YM2151 writes that follow
can be pinned on the voice that made them.

This is the ground truth the music decoder is checked against. Reading
the handlers statically has twice produced confident wrong answers
here; running them does not.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from wpcaudio.fmtrace import trace
from sound_map import load_rom

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roms")
    parser.add_argument("--command", type=lambda t: int(t, 0), default=0x0E)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--voice", type=int, help="only this YM channel")
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args()

    rom, _ = load_rom(args.roms)
    events = trace(rom, args.command, args.seconds)
    shown = 0
    for e in events:
        if args.voice is not None and e.channel != args.voice:
            continue
        keys = [("KON" if r == 0x08 and v & 0x78 else "KOFF" if r == 0x08 else
                 "KC=%02X" % v if 0x28 <= r < 0x30 else None) for r, v in e.writes]
        keys = [k for k in keys if k]
        print("t%5d  ch%d  %02X:%04X  op %02X ($%02X)  %-18s %s"
              % (e.tick, e.channel, e.bank, e.address, e.opcode, e.opcode & 0x3F,
                 e.raw.hex(" "), " ".join(keys)))
        shown += 1
        if shown >= args.limit:
            break


if __name__ == "__main__":
    main()
