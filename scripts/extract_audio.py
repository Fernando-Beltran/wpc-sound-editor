"""Pulls one region of a WPC sound ROM out to a WAV file.

    python scripts/extract_audio.py ROM 0x2800 0x5800 --format dac -o out/x.wav

Rates are not yet calibrated against hardware: the DAC playback rate and
the CVSD bit clock both come from timers on the sound board, so a wrong
value changes pitch and duration but not intelligibility.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wpcaudio.cvsd import Hc55516Decoder, unpack_bits
from wpcaudio.dac import decode_dac
from wpcaudio.filters import lowpass, to_int16
from wpcaudio.wavio import write_wav


def _number(text):
    return int(text, 0)


def extract(rom_path, offset, length, fmt, rate, out_path, lsb_first=False,
            cutoff=3400.0):
    data = pathlib.Path(rom_path).read_bytes()[offset:offset + length]
    if not data:
        raise SystemExit("la region esta fuera del fichero")

    if fmt == "dac":
        samples = decode_dac(data)
    else:
        samples = Hc55516Decoder().decode(unpack_bits(data, msb_first=not lsb_first))
        # The board low-passes the decoder output; without it the audio
        # carries roughly 20 dB more hiss than the hardware produces.
        if cutoff:
            samples = to_int16(lowpass(samples, rate, cutoff=cutoff))

    write_wav(out_path, samples, rate)
    return samples


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rom")
    parser.add_argument("offset", type=_number)
    parser.add_argument("length", type=_number)
    parser.add_argument("--format", choices=("dac", "cvsd"), required=True)
    parser.add_argument("--rate", type=int, default=None,
                        help="samples/s for dac, bits/s for cvsd")
    parser.add_argument("--lsb-first", action="store_true",
                        help="unpack cvsd bits least significant first")
    parser.add_argument("--cutoff", type=float, default=3400.0,
                        help="reconstruction low-pass in Hz; 0 disables it")
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args()

    rate = args.rate or (11025 if args.format == "dac" else 22050)
    samples = extract(args.rom, args.offset, args.length, args.format,
                      rate, args.out, args.lsb_first, args.cutoff)
    print("%s  %d muestras  %.2f s" % (args.out, len(samples), len(samples) / rate))


if __name__ == "__main__":
    main()
