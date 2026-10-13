"""Replaces sounds in a ROM set with your own WAVs, in place.

    python scripts/replace_sound.py <romdir> --set cvsd:12=mivoz.wav --dry-run
    python scripts/replace_sound.py <romdir> --set cvsd:12=mivoz.wav \
        --out-dir out/rom_parcheada

A block cannot grow: its end address is in the ROM's table and the next
block starts there. So anything longer is truncated and anything
shorter is padded with silence, and this says so before writing. Run it
with --dry-run first to see what each block holds.

The original images are never touched: the patched set is written to
--out-dir. After writing, each replaced block is read back out of the
patched ROM and decoded to a WAV under `preview/`, so you can hear what
actually landed in the ROM rather than what you handed it.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wpcaudio.cvsd import Hc55516Decoder, unpack_bits
from wpcaudio.dac import decode_dac
from wpcaudio.filters import lowpass, to_int16
from wpcaudio.importer import prepare
from wpcaudio.soundrom import duration
from wpcaudio.wavio import read_wav, write_wav

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sound_map import DAC_RATE, load_rom


def parse_assignment(text):
    """`cvsd:12=path/to.wav` -> ("cvsd", 12, path)."""
    target, separator, path = text.partition("=")
    kind, _, index = target.partition(":")
    if not separator or kind not in ("dac", "cvsd") or not index.isdigit():
        raise SystemExit("usa --set tipo:indice=fichero.wav, no %r" % text)
    return kind, int(index), pathlib.Path(path)


def decode(block, data, dac_rate, bit_rate, cutoff):
    if block.kind == "dac":
        samples, rate = decode_dac(data), dac_rate
    else:
        samples, rate = Hc55516Decoder().decode(unpack_bits(data)), bit_rate
    if cutoff:
        samples = to_int16(lowpass(samples, rate, cutoff))
    return samples, rate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roms", help="directory holding the U14/U15/U18 images")
    parser.add_argument("--set", dest="assignments", action="append", required=True,
                        metavar="KIND:INDEX=WAV", help="may be given more than once")
    parser.add_argument("--out-dir", help="where the patched images go")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the fit and write nothing")
    parser.add_argument("--rate", type=int, default=DAC_RATE, help="DAC sample rate")
    parser.add_argument("--bit-rate", type=int, help="CVSD bit clock (default: twice --rate)")
    parser.add_argument("--cutoff", type=float, default=3400.0,
                        help="reconstruction low-pass for the previews; 0 disables it")
    arguments = parser.parse_args()

    if not arguments.dry_run and not arguments.out_dir:
        parser.error("hace falta --out-dir, o --dry-run para no escribir nada")

    rom, _ = load_rom(arguments.roms)
    blocks = {(b.kind, b.index): b for b in rom.blocks()}
    bit_rate = arguments.bit_rate or 2 * arguments.rate

    patched = []
    for assignment in arguments.assignments:
        kind, index, path = parse_assignment(assignment)
        block = blocks.get((kind, index))
        if block is None:
            raise SystemExit("no existe el bloque %s:%d" % (kind, index))
        if not block.size:
            raise SystemExit("el bloque %s:%d esta vacio en la tabla" % (kind, index))

        samples, rate = read_wav(path)
        data, report = prepare(block, samples, rate,
                               dac_rate=arguments.rate, bit_rate=bit_rate)

        print("%s:%d  %s" % (kind, index, path))
        print("    el bloque admite %d bytes, %.2f s  (%s 0x%06X)"
              % (report.size, report.capacity_seconds or 0.0,
                 block.chip.upper(), block.offset))
        print("    tu audio ocupa   %d bytes, %.2f s a %d Hz"
              % (report.payload, len(samples) / float(rate), rate))
        if report.truncated:
            cut = duration(kind, report.truncated, bit_rate if kind == "cvsd" else arguments.rate)
            print("    SE RECORTAN %d bytes (%.2f s) del final" % (report.truncated, cut))
        if report.padded:
            print("    se rellenan %d bytes de silencio al final" % report.padded)

        if not arguments.dry_run:
            rom.write(block, data)
            patched.append(block)

    if arguments.dry_run:
        print("\n--dry-run: no se ha escrito nada")
        return

    out = pathlib.Path(arguments.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fixed = rom.fix_checksums()
    if fixed:
        print("checksum de arranque corregido en %d paginas" % len(fixed))
    rom.save({name: out / ("TZU%s_patched.ROM" % name[1:]) for name in rom.chips})

    # Read each replaced block back out of the patched images, so the
    # preview is what the ROM holds and not what we handed it.
    reread, _ = load_rom(out)
    previews = out / "preview"
    previews.mkdir(exist_ok=True)
    for block in patched:
        samples, rate = decode(block, reread.extract(block),
                               arguments.rate, bit_rate, arguments.cutoff)
        path = previews / ("%s_%03d.wav" % (block.kind, block.index))
        write_wav(path, samples, rate)
        print("escucha lo que quedo en la ROM: %s" % path)

    print("\nimagenes parcheadas en %s" % out)


if __name__ == "__main__":
    main()
