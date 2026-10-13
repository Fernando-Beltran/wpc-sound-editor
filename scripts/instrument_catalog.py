"""Collects every instrument the game's music uses.

    python scripts/instrument_catalog.py <romdir> -o mapas/instrumentos_tz_l2.json

An instrument here is a voice's set-up: the bytes from its program's
start to its first music opcode -- the CALLs to the `$17`-register
subroutines that load the operators, the release gap, a volume offset,
sometimes a detune. Running every music command once and keeping each
distinct set-up of a voice that then plays notes gives the palette a
compiled song can pick from. The set-up's CALLs point into the
directory bank and the fixed bank, which is where a compiled part runs
it from, so the bytes can be copied as they are.
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from wpcaudio import fmtrace, script
from sound_map import load_rom


LOADS = {0x13, 0x17, 0x18, 0x19, 0x1F}     # CALL, and direct register writes


def signature(body):
    """What makes a set-up sound like itself: the instrument it loads.

    The CALL targets (and direct register writes) are the timbre; the
    release gap, volume offset and detune around them only vary it. A
    set-up that loads nothing keeps whatever the channel last had, so
    it is no instrument at all.
    """
    sig, at = [], 0
    while at < len(body):
        op = body[at] & 0x3F
        size = {0x13: 3, 0x17: 3, 0x18: 3, 0x19: 3, 0x1F: 11}.get(op, 2 if op in (0x3B, 0x0F, 0x0C, 0x22, 0x38) else 3 if op in (0x27, 0x28, 0x1B, 0x10) else 1)
        if op in LOADS:
            sig.append(body[at:at + size].hex())
        at += size
    return tuple(sig)


def condense(catalog):
    """One entry per instrument: its most used variant, all its uses."""
    groups = {}
    for entry in catalog:
        sig = signature(bytes.fromhex(entry["bytes"]))
        if sig:
            groups.setdefault(sig, []).append(entry)
    out = []
    for variants in groups.values():
        best = max(variants, key=lambda e: len(e["uses"]))
        out.append({"bytes": best["bytes"], "variants": len(variants),
                    "uses": [use for e in variants for use in e["uses"]]})
    out.sort(key=lambda e: -len(e["uses"]))
    for i, entry in enumerate(out):
        entry["id"] = i
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roms")
    parser.add_argument("--map", default="mapas/tz_l2.json")
    parser.add_argument("-o", "--output", default="mapas/instrumentos_tz_l2.json")
    parser.add_argument("--condense-only", action="store_true",
                        help="re-condense an existing raw catalogue without running the board")
    args = parser.parse_args()

    out = pathlib.Path(args.output)
    raw = out.with_name(out.stem + "_bruto.json")
    if args.condense_only:
        catalog = condense(json.loads(raw.read_text(encoding="utf-8")))
        out.write_text(json.dumps(catalog, indent=1), encoding="utf-8")
        print("%s: %d instrumentos" % (out, len(catalog)))
        return

    rom, _ = load_rom(args.roms)
    u18 = rom.chips["u18"]
    rows = json.loads(pathlib.Path(args.map).read_text(encoding="utf-8"))
    music = sorted((r for r in rows if r.get("keyon")), key=lambda r: -r["keyon"])

    found = {}
    for n, row in enumerate(music):
        command = row["cmd"]
        try:
            programs = {p.address for p in script.programs_of(u18, command)}
        except script.NoPrograms:
            continue
        for voice in fmtrace.voices(rom, command, seconds=1.0):
            if voice.address not in programs or not voice.notes:
                continue
            start = script.rom_offset(u18, voice.address)
            body = bytes(u18[start:start + voice.prologue - voice.address])
            entry = found.setdefault(body.hex(), {"bytes": body.hex(), "uses": []})
            entry["uses"].append({"command": command, "program": voice.address,
                                  "channel": voice.channel})
        print("%3d/%d  $%02X  %d instrumentos" % (n + 1, len(music), command, len(found)),
              flush=True)

    raw.write_text(json.dumps(list(found.values()), indent=1), encoding="utf-8")
    catalog = condense(list(found.values()))
    out.write_text(json.dumps(catalog, indent=1), encoding="utf-8")
    print("%s: %d instrumentos" % (out, len(catalog)))


if __name__ == "__main__":
    main()
