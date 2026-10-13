"""Compiles a MIDI file into a music command, and checks it by ear's proxy.

    python scripts/midi_to_rom.py <romdir> song.mid --command 0x0E --block cvsd:101 \\
        --out-dir out/midi_rom

The command keeps its voices' instrument set-ups and plays the MIDI's
notes instead of its own; the note streams go into the borrowed sample
block. Afterwards the patched set is run on the emulated board, what it
plays is exported back to MIDI, and every input note is looked for in
it -- the round trip is the check, not the compiler's own say-so.

`--demo` writes a known test song instead of reading one.
"""

import argparse
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from wpcaudio import fmcompile, fmmidi, fmtrace, midi, script
from wpcaudio.midi import Note
from wpcaudio.soundrom import SoundRom
from sound_map import load_rom

SILENCE = {"dac": 0x80, "cvsd": 0x55}


def demo_song():
    """A scale over a bass line and two-note chords: easy to check."""
    notes, beat = [], 0.25
    scale = [60, 62, 64, 65, 67, 69, 71, 72, 71, 69, 67, 65, 64, 62, 60, 60]
    for i, pitch in enumerate(scale):
        notes.append(Note(i * beat, beat * 0.8, pitch, 100, 0))
    for i, pitch in enumerate([36, 43, 41, 36]):
        notes.append(Note(i * 4 * beat, 4 * beat * 0.9, pitch, 100, 1))
    for i, (a, b) in enumerate([(52, 55), (55, 59), (53, 57), (52, 55)]):
        notes.append(Note(i * 4 * beat, 2 * beat, a, 90, 2))
        notes.append(Note(i * 4 * beat, 2 * beat, b, 90, 2))
    return notes


def apply_plan(rom, plan, block):
    u18 = rom.chips["u18"]
    for address, data in plan.writes:
        offset = script.rom_offset(u18, address)
        u18[offset:offset + len(data)] = data
    chip, offset = rom.resolve(block.bank, block.start)
    image = rom.chips[chip]
    image[offset:offset + block.size] = bytes([SILENCE[block.kind]]) * block.size
    for address, data in plan.streams:
        chip, offset = rom.resolve(block.bank, address)
        rom.chips[chip][offset:offset + len(data)] = data


def compare(wanted, got, tolerance=0.05):
    """Finds every wanted note in what was played; returns a report.

    Each voice starts a little late -- the board runs its instrument
    set-up first, as the original music does -- so the check allows a
    common latency (the median offset) plus a tolerance around it.
    """
    wanted = [n for n in wanted if n.channel != fmmidi.DRUMS]
    got = [n for n in got if n.channel != fmmidi.DRUMS]
    if not wanted or not got:
        return {"matched": 0, "wanted": len(wanted)}
    first = min(n.start for n in got) - min(n.start for n in wanted)
    diffs = []
    for note in wanted:
        best = min((g for g in got if g.pitch == note.pitch),
                   key=lambda g: abs(g.start - first - note.start), default=None)
        if best is not None:
            diffs.append(best.start - note.start)
    offset = statistics.median(diffs)
    matched, errors, lengths, pool = 0, [], [], list(got)
    for note in wanted:
        best = min((g for g in pool if g.pitch == note.pitch),
                   key=lambda g: abs(g.start - offset - note.start), default=None)
        if best is not None and abs(best.start - offset - note.start) < tolerance:
            matched += 1
            errors.append(abs(best.start - offset - note.start))
            lengths.append(best.length - note.length)
            pool.remove(best)
    return {"matched": matched, "wanted": len(wanted), "offset": offset,
            "worst_start_ms": 1000 * max(errors or [0]),
            "median_length_ms": 1000 * statistics.median(lengths or [0])}


def render(rom, command, seconds, target):
    """What the patched command sounds like, through VGMPlay."""
    from wpcaudio.board import CPU_CLOCK, YM_CLOCK
    from wpcaudio.music import record
    from wpcaudio.vgm import write_vgm
    from wpcaudio.vgmplay import render as play
    log, _ = record(rom, command, seconds)
    vgm = target.with_suffix(".vgm")
    write_vgm(vgm, log, cpu_clock=CPU_CLOCK, ym_clock=YM_CLOCK)
    wav = play(vgm)
    if wav != target:
        wav.replace(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roms")
    parser.add_argument("midi", nargs="?")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--command", type=lambda t: int(t, 0), default=0x0E)
    parser.add_argument("--block", default="cvsd:101")
    parser.add_argument("--keep-drums", action="store_true",
                        help="keep the command's own DAC drums (they belong to its old song)")
    parser.add_argument("--once", action="store_true", help="play once, do not loop")
    parser.add_argument("--channels", help="MIDI channels by priority, 1-based: 1,2,5")
    parser.add_argument("--seconds", type=float, help="only the first N seconds")
    parser.add_argument("--transpose", type=int, default=0, help="semitones")
    parser.add_argument("--out-dir", default="out/midi_rom")
    args = parser.parse_args()

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if args.demo or not args.midi:
        notes = demo_song()
        (out / "entrada.mid").write_bytes(midi.write(notes))
    else:
        notes = midi.read(pathlib.Path(args.midi).read_bytes())
    if args.seconds:
        notes = [n._replace(length=min(n.length, args.seconds - n.start))
                 for n in notes if n.start < args.seconds]
    if args.transpose:
        notes = [n._replace(pitch=n.pitch + args.transpose) if n.channel != 9 else n
                 for n in notes]
    priority = [int(c) - 1 for c in args.channels.split(",")] if args.channels else None

    original, images = load_rom(args.roms)
    rom = SoundRom(**images)
    kind, index = args.block.split(":")
    block = next(b for b in rom.blocks() if b.kind == kind and b.index == int(index))

    programs = {p.address for p in script.programs_of(rom.chips["u18"], args.command)}
    found = [v for v in fmtrace.voices(rom, args.command) if v.address in programs]
    fm = [fmcompile.Voice(v.address, v.channel, v.prologue) for v in found if v.notes]
    drums = [v.address for v in found if v.samples and not v.notes]
    control = [v.address for v in found if not v.samples and not v.notes]
    users = script.program_users(rom.chips["u18"])
    starts = list(users)

    plan = fmcompile.compile_song(
        notes, fm, block, loop=not args.once,
        silence=control + ([] if args.keep_drums else drums),
        room=lambda address: script.room(address, starts), priority=priority)
    shared = sorted({c for p in programs for c in users.get(p, ()) if c != args.command})

    print("comando $%02X: %d voces FM en %d partes, bateria %s, control %s"
          % (args.command, len(fm), len(fmcompile.parts(fm)),
             [hex(a) for a in drums], [hex(a) for a in control]))
    used = sum(len(d) for _, d in plan.streams)
    print("bloque %s: %d de %d bytes" % (args.block, used, block.size))
    print("notas descartadas por falta de voces: %d, fuera de rango: %d"
          % (len(plan.dropped), len(plan.out_of_range)))
    import collections
    kept = collections.Counter(n.channel + 1 for n in notes if n.channel != 9)
    lost = collections.Counter(n.channel + 1 for n in plan.dropped)
    print("  por canal (colocadas/total): " + ", ".join(
        "%d: %d/%d" % (c, kept[c] - lost[c], kept[c]) for c in sorted(kept)))
    if shared:
        print("cambian tambien los comandos: %s" % ", ".join("$%02X" % c for c in shared))

    apply_plan(rom, plan, block)
    print("paginas con checksum corregido:", [hex(b) for b in rom.fix_checksums()])
    for chip, image in rom.chips.items():
        (out / ("TZ%s_midi.ROM" % chip.upper())).write_bytes(bytes(image))

    played = fmmidi.export(rom, args.command, seconds=max(n.start + n.length for n in notes) + 1)
    (out / "salida.mid").write_bytes(midi.write(played))
    placed = [n for n in notes if n not in set(plan.dropped) | set(plan.out_of_range)]
    report = compare(placed, played)
    print("ida y vuelta: %(matched)d de %(wanted)d notas encontradas" % report)
    if report.get("matched"):
        print("  latencia %.1f ms; peor desviacion %.1f ms; duracion mediana %+.1f ms"
              % (1000 * report["offset"], report["worst_start_ms"], report["median_length_ms"]))
    length = max(n.start + n.length for n in notes) + 1
    print("audio:", render(rom, args.command, 2 * length if not args.once else length,
                           out / "salida.wav"))


if __name__ == "__main__":
    main()
