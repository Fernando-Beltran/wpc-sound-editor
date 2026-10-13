"""Lists the sample directory of a sound ROM set, and extracts blocks.

    python scripts/sound_map.py TZ/extracted/Revision_L_2_sound_ROMs --list
    python scripts/sound_map.py <dir> --extract cvsd:0-9 --out out/voz
    python scripts/sound_map.py <dir> --extract dac:12 --rate 11025

Block boundaries are exact: they come from the tables U18's own code
reads (see wpcaudio/soundrom.py). The playback rates are not -- both
come from a YM2151 timer on the board and are still uncalibrated, so a
wrong value changes pitch and duration, never intelligibility.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wpcaudio.cvsd import Hc55516Decoder, unpack_bits
from wpcaudio.dac import decode_dac
from wpcaudio.filters import lowpass, to_int16
from wpcaudio.soundrom import SoundRom, duration
from wpcaudio.wavio import write_wav

# Uncalibrated, and the FIRQ structure ties them together: each
# interrupt emits two DAC samples and four CVSD bits, so the bit clock
# runs at twice the sample rate.
DAC_RATE = 11025
CVSD_BIT_RATE = 2 * DAC_RATE


def load_rom(directory):
    """Finds the three images in a directory by size."""
    images = {}
    for path in sorted(pathlib.Path(directory).iterdir()):
        if path.suffix.lower() not in (".rom", ".bin", ".u14", ".u15", ".u18"):
            continue
        name = path.name.lower()
        for socket in ("u14", "u15", "u18"):
            if socket in name:
                images[socket] = path.read_bytes()
    if "u18" not in images:
        raise SystemExit("no encuentro la imagen de U18 en %s" % directory)
    return SoundRom(**images), images


def parse_selection(text, blocks):
    """`cvsd:3`, `dac:0-9` or `cvsd` for the whole table."""
    kind, _, span = text.partition(":")
    chosen = [b for b in blocks if b.kind == kind]
    if not chosen:
        raise SystemExit("no hay bloques de tipo %r" % kind)
    if not span:
        return chosen

    first, _, last = span.partition("-")
    low = int(first, 0)
    high = int(last, 0) if last else low
    return [b for b in chosen if low <= b.index <= high]


def decode_run(kind, data, dac_rate, bit_rate, cutoff):
    """Decodes a whole run in one pass, so the codec carries its state
    across the joins the way the chip would."""
    if kind == "dac":
        samples, rate = decode_dac(data), dac_rate
    else:
        samples, rate = Hc55516Decoder().decode(unpack_bits(data)), bit_rate
    if cutoff:
        samples = to_int16(lowpass(samples, rate, cutoff))
    return samples, rate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roms", help="directory holding the U14/U15/U18 images")
    parser.add_argument("--list", action="store_true", help="print the directory")
    parser.add_argument("--extract", help="blocks to write as WAV, e.g. cvsd:0-9")
    parser.add_argument("--runs", action="store_true",
                        help="write each back-to-back run of blocks as one WAV. A run is "
                             "a 32 KB ROM page fill, not a phrase: handy for hearing the "
                             "joins, not a recording")
    parser.add_argument("--out", default="out", help="where the WAVs go")
    parser.add_argument("--rate", type=int, help="DAC sample rate (default %d)" % DAC_RATE)
    parser.add_argument("--bit-rate", type=int,
                        help="CVSD bit clock (default %d)" % CVSD_BIT_RATE)
    parser.add_argument("--cutoff", type=float, default=3400.0,
                        help="reconstruction low-pass; 0 disables it")
    arguments = parser.parse_args()

    rom, images = load_rom(arguments.roms)
    blocks = rom.blocks()
    dac_rate = arguments.rate or DAC_RATE
    bit_rate = arguments.bit_rate or (2 * dac_rate)

    if arguments.list or not arguments.extract:
        print("imagenes: " + ", ".join(
            "%s %d KB" % (s.upper(), len(d) // 1024) for s, d in sorted(images.items())))
        for kind in ("dac", "cvsd"):
            chosen = [b for b in blocks if b.kind == kind]
            total = sum(b.size for b in chosen)
            rate = dac_rate if kind == "dac" else bit_rate
            print("\n%s: %d bloques, %d bytes, %.1f s"
                  % (kind.upper(), len(chosen), total, duration(kind, total, rate)))
            for block in chosen:
                print("  %3d  banco $%02X  $%04X-$%04X  %6d B  %6.2f s  %s 0x%06X"
                      % (block.index, block.bank, block.start, block.end, block.size,
                         duration(kind, block.size, rate) or 0.0,
                         block.chip.upper(), block.offset))

    if not arguments.extract:
        return

    out = pathlib.Path(arguments.out)
    out.mkdir(parents=True, exist_ok=True)

    chosen = parse_selection(arguments.extract, blocks)
    if arguments.runs:
        wanted = {b.index for b in chosen}
        kind = chosen[0].kind
        groups = [c for c in rom.runs([b for b in blocks if b.kind == kind])
                  if any(b.index in wanted for b in c)]
        for chain in groups:
            data = b"".join(rom.extract(b) for b in chain)
            samples, rate = decode_run(kind, data, dac_rate, bit_rate, arguments.cutoff)
            path = out / ("%s_pagina_%03d_%dbloques.wav"
                          % (kind, chain[0].index, len(chain)))
            write_wav(path, samples, rate)
            print("  %s <- bloques %s, %.2f s"
                  % (path, [b.index for b in chain], len(samples) / rate))
        return

    for block in chosen:
        if not block.size:
            print("  %s %d: vacio, lo salto" % (block.kind, block.index))
            continue

        data = rom.extract(block)
        if block.kind == "dac":
            samples, rate = decode_dac(data), dac_rate
        else:
            decoder = Hc55516Decoder()
            samples, rate = decoder.decode(unpack_bits(data)), bit_rate

        if arguments.cutoff:
            samples = to_int16(lowpass(samples, rate, arguments.cutoff))

        path = out / ("%s_%03d_%s_%06X.wav" % (block.kind, block.index,
                                               block.chip, block.offset))
        write_wav(path, samples, rate)
        print("  %s -> %d muestras, %.2f s" % (path, len(samples), len(samples) / rate))


if __name__ == "__main__":
    main()
