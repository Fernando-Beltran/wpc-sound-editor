"""Derives how far each sound-script opcode advances the script pointer.

    python scripts/opcode_advances.py TZU18_L2.ROM

Every handler in the `$E1D8` dispatch table takes its operands from the
script pointer in `$06,U` and writes the updated pointer back, usually
through the shared tail at `$FF03` (`LDA ,X+` / `STA $04,U` / `STX
$06,U`). So the operand length can be read off the code instead of
guessed: load the pointer, follow the handler's branches, calls and
jumps while tracking what `,X+`, `,X++` and `LEAX n,X` add to it, and
record the total wherever `STX $06,U` happens.

That settles 38 of the 63 opcodes. The rest either never store the
pointer (they are no-ops or they stop the voice) or hide it behind a
helper this analysis does not follow. **Do not fill the gaps by
guessing**: see the project notes for the consistency search that was tried and
produced verifiably wrong lengths.
"""

import collections
import pathlib
import re
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from wpc6809.disasm import disassemble

bank = open(sys.argv[1] if len(sys.argv) > 1 else
            "TZ/extracted/Revision_L_2_sound_ROMs/TZU18_L2.ROM",
            "rb").read()[0x07C000:0x080000]
ORIGIN = 0xC000
def one(a):
    return disassemble(bank, origin=ORIGIN, start=a-ORIGIN, end=a-ORIGIN+5)[0]

TERM = {"RTS","RTI","SWI","SWI2","SWI3"}
UNCOND = {"BRA","LBRA","JMP"}
COND = {"BRN","BHI","BLS","BCC","BCS","BNE","BEQ","BVC","BVS","BPL","BMI","BGE","BLT","BGT","BLE"}

def advances(entry, limit=40000):
    """Cuanto avanza el puntero de script ($06,U) en este handler."""
    found, seen = set(), set()
    # En anchura: con una pila, los caminos interesantes quedan al final
    # de la cola y se agota el presupuesto explorando rutinas ajenas.
    work = collections.deque([(entry, None, ())])   # pc, delta (None = X no es el puntero), pila
    steps = 0
    while work and steps < limit:
        pc, delta, stack = work.popleft(); steps += 1
        while ORIGIN <= pc <= 0xFFFF:
            key = (pc, delta, stack)
            if key in seen: break
            seen.add(key)
            ins = one(pc); m, op = ins.mnemonic, ins.operand

            if m == "LDX" and op == "$06,U":        delta = 0
            elif m == "STX" and op == "$06,U":
                # No se corta aqui: varios handlers guardan el puntero y luego
                # avanzan otra vez (tipicamente con JSR $FF03), asi que el
                # primer guardado no es el definitivo.
                if delta is not None: found.add(delta)
            elif m == "LDX" and op != "$06,U":      delta = None
            elif m == "PSHS" and "X" in op.split(","): stack = stack + (delta,)
            elif m == "PULS" and "X" in op.split(","):
                if stack: delta, stack = stack[-1], stack[:-1]
            elif m == "LEAX" and delta is not None:
                g = re.match(r"^(-?)\$([0-9A-F]+),X$", op)
                if g: delta += (-1 if g.group(1) else 1) * int(g.group(2), 16)
                else: delta = None
            elif delta is not None and op.endswith(",X++"): delta += 2
            elif delta is not None and op.endswith(",X+"):  delta += 1

            if m in COND and ins.target: work.append((ins.target, delta, stack))
            # Solo se entra en una llamada si X sigue siendo el puntero de
            # script: si no, el ayudante no puede decir nada del avance y
            # seguirlo hace estallar la exploracion.
            if (m in ("JSR","BSR","LBSR") and delta is not None
                    and ins.target and ORIGIN <= ins.target <= 0xFFFF):
                work.append((ins.target, delta, stack))
            if m in UNCOND and ins.target: pc = ins.target; continue
            if m in TERM or m == "FCB": break
            pc += ins.length
    return sorted(found)

def fw(a): o=a-ORIGIN; return (bank[o]<<8)|bank[o+1]
print("op  handler  avance")
for i in range(63):
    p = fw(0xE1D8 + i*2)
    a = advances(p)
    print("%02X  $%04X   %-18s max %s" % (i, p, a or "-", max(a) if a else "-"))
