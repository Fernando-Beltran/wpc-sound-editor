"""Learns what a sound-script byte does, by changing it and listening.

    python scripts/probe_music.py <romdir> --command 0x0E --bytes 24

Two static attempts at this VM produced confident wrong answers, so
this does not read the script at all. It runs the board, changes one
byte of a voice program, runs it again, and reports which YM2151
registers came out different. A byte that moves register `$28` is a
note; one that moves `$30`-`$37` is a fine pitch; one that changes
nothing in the registers but shifts every later write in time is a
duration.

That is the whole idea: with an emulator, a question that took days of
disassembly takes one run. Nothing here infers; it observes.
"""

import argparse
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wpcaudio.board import CPU_CLOCK, SoundBoard
from wpcaudio.music import record
from wpcaudio.soundrom import SoundRom

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sound_map import load_rom

# Which registers are what, from the YM2151's own map. Enough to say
# what a change touched without claiming more than is observed.
ROLES = [
    (0x01, 0x01, "test/LFO reset"),
    (0x08, 0x08, "key on/off"),
    (0x0F, 0x0F, "noise"),
    (0x10, 0x14, "timers"),
    (0x18, 0x1B, "LFO"),
    (0x20, 0x27, "channel: algorithm, feedback, panning"),
    (0x28, 0x2F, "note (key code)"),
    (0x30, 0x37, "fine pitch (key fraction)"),
    (0x38, 0x3F, "modulation sensitivity"),
    (0x40, 0x5F, "operator detune and multiply"),
    (0x60, 0x7F, "operator level"),
    (0x80, 0x9F, "attack rate"),
    (0xA0, 0xBF, "first decay"),
    (0xC0, 0xDF, "second decay"),
    (0xE0, 0xFF, "sustain and release"),
]


def role(register):
    for low, high, name in ROLES:
        if low <= register <= high:
            return name
    return "?"


def signature(log):
    """What a run wrote, as something two runs can be compared by."""
    return Counter((write.register, write.value) for write in log)


def timing(log):
    """When it wrote, so a change in duration shows up as a shift."""
    return [write.cycle for write in log]


def rom_offset(address, directory_bank_offset, fixed_offset):
    """Where a script address lives in the U18 image."""
    if address >= 0xC000:
        return fixed_offset + address - 0xC000
    return directory_bank_offset + address - 0x4000


def programs_of(rom, command):
    """The voice programs a command starts, with their ROM offsets."""
    page = 0x060000
    fixed = len(rom.chips["u18"]) - 0x4000

    def byte(address):
        return rom.chips["u18"][rom_offset(address, page, fixed)]

    def word(address):
        return (byte(address) << 8) | byte(address + 1)

    table = word(0x400F)
    kind, param = byte(table + command * 2), byte(table + command * 2 + 1)

    if kind == 4:
        head = word(word(0x4007) + param * 2)
        mask = byte(head)
        pointers = [word(head + 1 + 2 * i) for i in range(bin(mask).count("1"))]
    elif kind == 1:
        entry = word(word(0x4011) + param * 2)
        pointers = [word(entry + i * 2) for i in range(10)]
    else:
        pointers = []

    seen, out = set(), []
    for pointer in pointers:
        if 0x4000 <= pointer <= 0xFFFF and pointer not in seen:
            seen.add(pointer)
            out.append((pointer, rom_offset(pointer, page, fixed)))
    return kind, param, out


def run(rom, command, seconds):
    log, _ = record(rom, command, seconds)
    return log


def channels_of(registers):
    """Which YM2151 channels a set of registers belongs to.

    Most of its map is eight registers wide, one per channel, so a
    change confined to one channel is a different kind of event from
    one that sweeps all eight -- usually a single note against the
    script having desynchronised.
    """
    channels = set()
    for register in registers:
        if 0x20 <= register <= 0x3F or 0x40 <= register <= 0xFF:
            channels.add(register & 0x07)
    return channels


def describe(before, after, before_times, after_times):
    """What changed between two runs, said as briefly as it can be."""
    touched = {register for register, _ in ((after - before) | (before - after))}
    if not touched:
        if before_times == after_times:
            return "sin efecto"
        shift = ((after_times[-1] - before_times[-1]) / float(CPU_CLOCK)
                 if after_times and before_times else 0.0)
        return "solo el tiempo (%+.3f s)" % shift

    names = sorted({role(register) for register in touched})
    channels = channels_of(touched)
    # Everything moving at once means the script lost its place rather
    # than that one byte meaning everything.
    if len(names) > 6:
        return "se descarrila (%d registros, %d canales)" % (len(touched), len(channels))

    where = ("canal %s" % ",".join(str(c) for c in sorted(channels))) if len(channels) <= 2 else "todos los canales"
    return "%s — %s" % ("; ".join(names), where)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roms")
    parser.add_argument("--command", type=lambda t: int(t, 0), required=True)
    parser.add_argument("--program", type=int, default=0,
                        help="which of the command's voice programs")
    parser.add_argument("--bytes", type=int, default=24,
                        help="how many bytes of it to probe")
    parser.add_argument("--seconds", type=float, default=1.2)
    parser.add_argument("--delta", type=lambda t: int(t, 0), default=1,
                        help="what to add to each byte")
    arguments = parser.parse_args()

    rom, _ = load_rom(arguments.roms)
    kind, param, found = programs_of(rom, arguments.command)
    if not found:
        raise SystemExit("el comando $%02X no arranca programas de voz (tipo %d)"
                         % (arguments.command, kind))
    if arguments.program >= len(found):
        raise SystemExit("solo hay %d programas" % len(found))

    address, offset = found[arguments.program]
    print("comando $%02X: tipo %d, parametro $%02X, %d programas"
          % (arguments.command, kind, param, len(found)))
    print("sondeando el programa $%04X (U18 0x%06X), %d bytes\n"
          % (address, offset, arguments.bytes))

    base_log = run(rom, arguments.command, arguments.seconds)
    base = signature(base_log)
    base_times = timing(base_log)
    print("referencia: %d escrituras\n" % len(base_log))

    original = bytes(rom.chips["u18"])
    for position in range(arguments.bytes):
        was = original[offset + position]
        now = (was + arguments.delta) & 0xFF
        rom.chips["u18"][offset + position] = now
        try:
            log = run(rom, arguments.command, arguments.seconds)
            notes = describe(base, signature(log), base_times, timing(log))
        except Exception as problem:                      # noqa: BLE001
            notes = "se descarrila: %s" % problem
        finally:
            rom.chips["u18"][offset + position] = was

        print("  +%-2d  $%04X  %02X->%02X  %s"
              % (position, address + position, was, now, notes))


if __name__ == "__main__":
    main()
