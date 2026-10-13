"""Disassembles a region of a sound ROM to a 6809 listing.

The region of interest is U18's fixed bank, ROM `0x07C000`-`0x07FFFF`
mapped to CPU `$C000`-`$FFFF`, which is where the reset and interrupt
vectors live and therefore where the sound command dispatch table has
to be reachable from:

    python scripts/disasm_rom.py TZU18_L2.ROM --bank fixed -o out/u18_fixed.asm
    python scripts/disasm_rom.py TZU18_L2.ROM --offset 0x7C000 --origin 0xC000 \
        --from-vector reset --length 0x100
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wpc6809.disasm import disassemble, format_listing

# The 6809 vector table, at the very top of the fixed bank.
VECTORS = {
    "swi3": 0xFFF2,
    "swi2": 0xFFF4,
    "firq": 0xFFF6,
    "irq": 0xFFF8,
    "swi": 0xFFFA,
    "nmi": 0xFFFC,
    "reset": 0xFFFE,
}


def parse_number(text):
    return int(text, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom")
    parser.add_argument(
        "--bank",
        choices=["fixed"],
        help="shorthand for the 16 KB fixed bank at 0x07C000, mapped to $C000",
    )
    parser.add_argument("--offset", type=parse_number, default=0,
                        help="where the mapped region starts in the ROM file")
    parser.add_argument("--size", type=parse_number,
                        help="how much of the ROM to map (default: to the end)")
    parser.add_argument("--origin", type=parse_number, default=0,
                        help="the CPU address the region is mapped at")
    parser.add_argument("--start", type=parse_number,
                        help="CPU address to begin disassembling at")
    parser.add_argument("--from-vector", choices=sorted(VECTORS),
                        help="begin at the address this vector points to")
    parser.add_argument("--length", type=parse_number,
                        help="how many bytes to disassemble (default: to the end)")
    parser.add_argument("-o", "--output", help="write the listing here instead of stdout")
    arguments = parser.parse_args()

    if arguments.bank == "fixed":
        arguments.offset = arguments.offset or 0x07C000
        arguments.size = arguments.size or 0x4000
        arguments.origin = arguments.origin or 0xC000

    with open(arguments.rom, "rb") as rom:
        rom.seek(arguments.offset)
        data = rom.read(arguments.size) if arguments.size else rom.read()

    if not data:
        parser.error("no hay datos en 0x%X" % arguments.offset)

    # The whole region is mapped; --start, --from-vector and --length
    # only choose the window to disassemble inside it.
    if arguments.from_vector:
        vector = VECTORS[arguments.from_vector] - arguments.origin
        if not 0 <= vector < len(data) - 1:
            parser.error("el vector %s cae fuera de la region mapeada"
                         % arguments.from_vector)
        start = ((data[vector] << 8) | data[vector + 1]) - arguments.origin
    elif arguments.start is not None:
        start = arguments.start - arguments.origin
    else:
        start = 0

    if not 0 <= start < len(data):
        parser.error("el arranque cae fuera de la region mapeada")

    end = min(start + arguments.length, len(data)) if arguments.length else None

    listing = format_listing(
        disassemble(data, origin=arguments.origin, start=start, end=end)
    )

    if arguments.output:
        with open(arguments.output, "w", encoding="utf-8") as handle:
            handle.write(listing + "\n")
        print("%s: %d lineas" % (arguments.output, listing.count("\n") + 1))
    else:
        print(listing)


if __name__ == "__main__":
    main()
