"""An MC6809 core, enough of one to run a WPC sound board's program.

The point is to stop guessing. The sound script VM in U18 has 63
opcodes and reverse-engineering their semantics one at a time kept
producing confident wrong answers; running the program instead costs
nothing in interpretation, because the ROM's own code does the
interpreting. Drive it with a command byte, watch what it writes to the
YM2151 and to the sample registers, and the answers fall out.

Decoding reuses `wpc6809.disasm`'s opcode tables, so the disassembler
and the emulator cannot disagree about what an opcode is -- they read
the same table. This module only adds what each one *does*.

Cycle counts are per-mode approximations rather than the real per-
instruction table. Nothing here depends on exact timing: the sound
board's interrupt comes from a YM2151 timer, not from a cycle count.
"""

from wpc6809.disasm import (
    DIRECT, EXTENDED, IMMEDIATE, INDEXED, INHERENT, LONG_RELATIVE,
    PAGE1, PAGE2, PAGE3, REGISTER_LIST, REGISTER_PAIR, RELATIVE,
    _WORD_IMMEDIATE,
)

# Condition code bits.
E, F, H, I, N, Z, V, C = 0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02, 0x01

# Registers a TFR/EXG postbyte can name, by nibble.
_PAIR16 = {0x0: "d", 0x1: "x", 0x2: "y", 0x3: "u", 0x4: "s", 0x5: "pc"}
_PAIR8 = {0x8: "a", 0x9: "b", 0xA: "cc", 0xB: "dp"}

# Stack postbyte, low bit first; index 6 is the other stack pointer.
_STACK_ORDER = ("cc", "a", "b", "dp", "x", "y", None, "pc")

_CYCLES = {
    INHERENT: 2, IMMEDIATE: 2, DIRECT: 4, EXTENDED: 5, INDEXED: 5,
    RELATIVE: 3, LONG_RELATIVE: 5, REGISTER_LIST: 5, REGISTER_PAIR: 6,
}


class Memory:
    """Flat 64 KB of RAM. Subclass to map I/O and banked ROM."""

    def __init__(self, data=None):
        self.data = bytearray(0x10000) if data is None else data

    def read(self, address):
        return self.data[address & 0xFFFF]

    def write(self, address, value):
        self.data[address & 0xFFFF] = value & 0xFF


class Cpu:
    """The processor itself. `step()` runs one instruction."""

    def __init__(self, memory):
        self.memory = memory
        self.a = self.b = self.dp = 0
        self.x = self.y = self.u = self.s = self.pc = 0
        self.cc = F | I
        self.cycles = 0
        self.waiting = False          # SYNC / CWAI parked the CPU

    # -- registers --------------------------------------------------

    @property
    def d(self):
        return (self.a << 8) | self.b

    @d.setter
    def d(self, value):
        value &= 0xFFFF
        self.a, self.b = value >> 8, value & 0xFF

    # -- memory -----------------------------------------------------

    def read(self, address):
        return self.memory.read(address & 0xFFFF)

    def write(self, address, value):
        self.memory.write(address & 0xFFFF, value)

    def read16(self, address):
        return (self.read(address) << 8) | self.read(address + 1)

    def write16(self, address, value):
        self.write(address, (value >> 8) & 0xFF)
        self.write(address + 1, value & 0xFF)

    def _fetch(self):
        value = self.read(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return value

    def _fetch16(self):
        return (self._fetch() << 8) | self._fetch()

    # -- flags ------------------------------------------------------

    def _set(self, bits, on):
        self.cc = (self.cc | bits) if on else (self.cc & ~bits & 0xFF)

    def _nz8(self, value):
        self._set(N, value & 0x80)
        self._set(Z, value == 0)

    def _nz16(self, value):
        self._set(N, value & 0x8000)
        self._set(Z, value == 0)

    # -- addressing -------------------------------------------------

    @staticmethod
    def _signed(value, bits):
        sign = 1 << (bits - 1)
        return value - (1 << bits) if value & sign else value

    def _indexed_address(self):
        """Decodes an indexed postbyte and returns the effective address."""
        postbyte = self._fetch()
        which = ("x", "y", "u", "s")[(postbyte >> 5) & 3]
        base = getattr(self, which)

        if not postbyte & 0x80:
            return (base + self._signed(postbyte & 0x1F, 5)) & 0xFFFF

        mode = postbyte & 0x0F
        indirect = postbyte & 0x10

        if mode == 0x0:
            address = base
            setattr(self, which, (base + 1) & 0xFFFF)
        elif mode == 0x1:
            address = base
            setattr(self, which, (base + 2) & 0xFFFF)
        elif mode == 0x2:
            address = setattr(self, which, (base - 1) & 0xFFFF) or getattr(self, which)
        elif mode == 0x3:
            address = setattr(self, which, (base - 2) & 0xFFFF) or getattr(self, which)
        elif mode == 0x4:
            address = base
        elif mode == 0x5:
            address = (base + self._signed(self.b, 8)) & 0xFFFF
        elif mode == 0x6:
            address = (base + self._signed(self.a, 8)) & 0xFFFF
        elif mode == 0x8:
            address = (base + self._signed(self._fetch(), 8)) & 0xFFFF
        elif mode == 0x9:
            address = (base + self._signed(self._fetch16(), 16)) & 0xFFFF
        elif mode == 0xB:
            address = (base + self._signed(self.d, 16)) & 0xFFFF
        elif mode == 0xC:
            offset = self._signed(self._fetch(), 8)
            address = (self.pc + offset) & 0xFFFF
        elif mode == 0xD:
            offset = self._signed(self._fetch16(), 16)
            address = (self.pc + offset) & 0xFFFF
        elif mode == 0xF:
            address = self._fetch16()
        else:
            raise ValueError("postbyte indexado invalido $%02X en $%04X"
                             % (postbyte, self.pc))

        return self.read16(address) if indirect else address

    def _address(self, mode):
        if mode is DIRECT:
            return (self.dp << 8) | self._fetch()
        if mode is EXTENDED:
            return self._fetch16()
        if mode is INDEXED:
            return self._indexed_address()
        raise ValueError("modo sin direccion efectiva: %s" % mode)

    def _operand8(self, mode):
        if mode is IMMEDIATE:
            return self._fetch()
        return self.read(self._address(mode))

    def _operand16(self, mode):
        if mode is IMMEDIATE:
            return self._fetch16()
        return self.read16(self._address(mode))

    # -- the stacks -------------------------------------------------

    def _push8(self, which, value):
        pointer = (getattr(self, which) - 1) & 0xFFFF
        setattr(self, which, pointer)
        self.write(pointer, value)

    def _pull8(self, which):
        pointer = getattr(self, which)
        value = self.read(pointer)
        setattr(self, which, (pointer + 1) & 0xFFFF)
        return value

    def _push16(self, which, value):
        self._push8(which, value & 0xFF)
        self._push8(which, (value >> 8) & 0xFF)

    def _pull16(self, which):
        return (self._pull8(which) << 8) | self._pull8(which)

    def _push_set(self, which, postbyte):
        other = "u" if which == "s" else "s"
        # Pushed high bit first, so PC goes down first and CC last.
        for bit in range(7, -1, -1):
            if not postbyte & (1 << bit):
                continue
            name = other if bit == 6 else _STACK_ORDER[bit]
            if name in ("cc", "a", "b", "dp"):
                self._push8(which, getattr(self, name))
            else:
                self._push16(which, getattr(self, name))

    def _pull_set(self, which, postbyte):
        other = "u" if which == "s" else "s"
        for bit in range(8):
            if not postbyte & (1 << bit):
                continue
            name = other if bit == 6 else _STACK_ORDER[bit]
            if name in ("cc", "a", "b", "dp"):
                setattr(self, name, self._pull8(which))
            else:
                setattr(self, name, self._pull16(which))

    # -- interrupts -------------------------------------------------

    def reset(self):
        self.cc |= F | I
        self.dp = 0
        self.waiting = False
        self.pc = self.read16(0xFFFE)

    def _enter(self, vector, entire):
        self._set(E, entire)
        self._push_set("s", 0xFF if entire else 0x81)
        self.pc = self.read16(vector)
        self.waiting = False

    def irq(self):
        if self.cc & I:
            return False
        self._enter(0xFFF8, True)
        self.cc |= I
        return True

    def firq(self):
        if self.cc & F:
            return False
        self._enter(0xFFF6, False)
        self.cc |= F | I
        return True

    def nmi(self):
        self._enter(0xFFFC, True)
        self.cc |= F | I
        return True

    # -- execution --------------------------------------------------

    def step(self):
        """Runs one instruction and returns the cycles it took."""
        if self.waiting:
            self.cycles += 1
            return 1

        opcode = self._fetch()
        if opcode == 0x10:
            entry = PAGE2.get(self._fetch())
        elif opcode == 0x11:
            entry = PAGE3.get(self._fetch())
        else:
            entry = PAGE1[opcode]

        if entry is None:
            raise ValueError("opcode indefinido $%02X en $%04X"
                             % (opcode, (self.pc - 1) & 0xFFFF))

        mnemonic, mode = entry
        used = _CYCLES.get(mode, 2)
        self._execute(mnemonic, mode)
        self.cycles += used
        return used

    def run(self, cycles):
        """Runs at least `cycles` worth of instructions."""
        target = self.cycles + cycles
        while self.cycles < target:
            self.step()

    def _execute(self, mnemonic, mode):
        handler = getattr(self, "_op_" + mnemonic.lower(), None)
        if handler is not None:
            return handler(mode)

        # The register-flavoured forms: LDA/LDB, ADDA/ADDB and so on
        # differ only in which accumulator they touch, so one handler
        # covers both and the last letter picks the register.
        for suffix in ("a", "b"):
            if mnemonic.lower().endswith(suffix):
                handler = getattr(self, "_acc_" + mnemonic[:-1].lower(), None)
                if handler is not None:
                    return handler(mode, suffix)

        raise ValueError("instruccion sin implementar: %s" % mnemonic)

    # -- loads and stores -------------------------------------------

    def _acc_ld(self, mode, reg):
        value = self._operand8(mode)
        setattr(self, reg, value)
        self._nz8(value)
        self._set(V, False)

    def _acc_st(self, mode, reg):
        value = getattr(self, reg)
        self.write(self._address(mode), value)
        self._nz8(value)
        self._set(V, False)

    def _load16(self, mode, reg):
        value = self._operand16(mode)
        setattr(self, reg, value)
        self._nz16(value)
        self._set(V, False)

    def _store16(self, mode, reg):
        value = getattr(self, reg)
        self.write16(self._address(mode), value)
        self._nz16(value)
        self._set(V, False)

    def _op_ldd(self, mode):
        self._load16(mode, "d")

    def _op_std(self, mode):
        self._store16(mode, "d")

    def _op_ldx(self, mode):
        self._load16(mode, "x")

    def _op_stx(self, mode):
        self._store16(mode, "x")

    def _op_ldy(self, mode):
        self._load16(mode, "y")

    def _op_sty(self, mode):
        self._store16(mode, "y")

    def _op_ldu(self, mode):
        self._load16(mode, "u")

    def _op_stu(self, mode):
        self._store16(mode, "u")

    def _op_lds(self, mode):
        self._load16(mode, "s")

    def _op_sts(self, mode):
        self._store16(mode, "s")

    def _op_leax(self, mode):
        self.x = self._address(INDEXED)
        self._set(Z, self.x == 0)

    def _op_leay(self, mode):
        self.y = self._address(INDEXED)
        self._set(Z, self.y == 0)

    def _op_leau(self, mode):
        self.u = self._address(INDEXED)

    def _op_leas(self, mode):
        self.s = self._address(INDEXED)

    # -- arithmetic -------------------------------------------------

    def _add8(self, left, right, carry=0):
        total = left + right + carry
        self._set(H, ((left & 0xF) + (right & 0xF) + carry) & 0x10)
        self._set(C, total > 0xFF)
        result = total & 0xFF
        self._set(V, (~(left ^ right) & (left ^ result)) & 0x80)
        self._nz8(result)
        return result

    def _sub8(self, left, right, borrow=0):
        total = left - right - borrow
        self._set(C, total < 0)
        result = total & 0xFF
        self._set(V, ((left ^ right) & (left ^ result)) & 0x80)
        self._nz8(result)
        return result

    def _acc_add(self, mode, reg):
        setattr(self, reg, self._add8(getattr(self, reg), self._operand8(mode)))

    def _acc_adc(self, mode, reg):
        setattr(self, reg,
                self._add8(getattr(self, reg), self._operand8(mode), self.cc & C))

    def _acc_sub(self, mode, reg):
        setattr(self, reg, self._sub8(getattr(self, reg), self._operand8(mode)))

    def _acc_sbc(self, mode, reg):
        setattr(self, reg,
                self._sub8(getattr(self, reg), self._operand8(mode), self.cc & C and 1))

    def _acc_cmp(self, mode, reg):
        self._sub8(getattr(self, reg), self._operand8(mode))

    def _add16(self, left, right):
        total = left + right
        self._set(C, total > 0xFFFF)
        result = total & 0xFFFF
        self._set(V, (~(left ^ right) & (left ^ result)) & 0x8000)
        self._nz16(result)
        return result

    def _sub16(self, left, right):
        total = left - right
        self._set(C, total < 0)
        result = total & 0xFFFF
        self._set(V, ((left ^ right) & (left ^ result)) & 0x8000)
        self._nz16(result)
        return result

    def _op_addd(self, mode):
        self.d = self._add16(self.d, self._operand16(mode))

    def _op_subd(self, mode):
        self.d = self._sub16(self.d, self._operand16(mode))

    def _compare16(self, mode, reg):
        self._sub16(getattr(self, reg), self._operand16(mode))

    def _op_cmpd(self, mode):
        self._compare16(mode, "d")

    def _op_cmpx(self, mode):
        self._compare16(mode, "x")

    def _op_cmpy(self, mode):
        self._compare16(mode, "y")

    def _op_cmpu(self, mode):
        self._compare16(mode, "u")

    def _op_cmps(self, mode):
        self._compare16(mode, "s")

    def _op_mul(self, mode):
        self.d = self.a * self.b
        self._set(Z, self.d == 0)
        self._set(C, self.b & 0x80)

    def _op_sex(self, mode):
        self.a = 0xFF if self.b & 0x80 else 0x00
        self._nz16(self.d)

    def _op_abx(self, mode):
        self.x = (self.x + self.b) & 0xFFFF

    def _op_daa(self, mode):
        correction = 0
        if (self.a & 0x0F) > 9 or self.cc & H:
            correction |= 0x06
        if self.a > 0x99 or self.cc & C:
            correction |= 0x60
        total = self.a + correction
        self._set(C, total > 0xFF)
        self.a = total & 0xFF
        self._nz8(self.a)

    # -- logic ------------------------------------------------------

    def _acc_and(self, mode, reg):
        value = getattr(self, reg) & self._operand8(mode)
        setattr(self, reg, value)
        self._nz8(value)
        self._set(V, False)

    def _acc_or(self, mode, reg):
        value = getattr(self, reg) | self._operand8(mode)
        setattr(self, reg, value)
        self._nz8(value)
        self._set(V, False)

    def _acc_eor(self, mode, reg):
        value = getattr(self, reg) ^ self._operand8(mode)
        setattr(self, reg, value)
        self._nz8(value)
        self._set(V, False)

    def _acc_bit(self, mode, reg):
        self._nz8(getattr(self, reg) & self._operand8(mode))
        self._set(V, False)

    def _op_andcc(self, mode):
        self.cc &= self._fetch()

    def _op_orcc(self, mode):
        self.cc |= self._fetch()

    # -- read-modify-write ------------------------------------------

    def _modify(self, mode, operation, reg=None):
        if reg is not None:
            setattr(self, reg, operation(getattr(self, reg)))
            return
        address = self._address(mode)
        self.write(address, operation(self.read(address)))

    def _f_neg(self, value):
        result = (-value) & 0xFF
        self._set(V, value == 0x80)
        self._set(C, value != 0)
        self._nz8(result)
        return result

    def _f_com(self, value):
        result = (~value) & 0xFF
        self._nz8(result)
        self._set(V, False)
        self._set(C, True)
        return result

    def _f_lsr(self, value):
        self._set(C, value & 1)
        result = value >> 1
        self._nz8(result)
        return result

    def _f_ror(self, value):
        carry = self.cc & C
        self._set(C, value & 1)
        result = (value >> 1) | (0x80 if carry else 0)
        self._nz8(result)
        return result

    def _f_asr(self, value):
        self._set(C, value & 1)
        result = (value >> 1) | (value & 0x80)
        self._nz8(result)
        return result

    def _f_lsl(self, value):
        self._set(C, value & 0x80)
        result = (value << 1) & 0xFF
        self._set(V, (value ^ result) & 0x80)
        self._nz8(result)
        return result

    def _f_rol(self, value):
        carry = self.cc & C
        self._set(C, value & 0x80)
        result = ((value << 1) & 0xFF) | (1 if carry else 0)
        self._set(V, (value ^ result) & 0x80)
        self._nz8(result)
        return result

    def _f_dec(self, value):
        result = (value - 1) & 0xFF
        self._set(V, value == 0x80)
        self._nz8(result)
        return result

    def _f_inc(self, value):
        result = (value + 1) & 0xFF
        self._set(V, value == 0x7F)
        self._nz8(result)
        return result

    def _f_clr(self, value):
        self._set(N | V | C, False)
        self._set(Z, True)
        return 0

    def _f_tst(self, value):
        self._nz8(value)
        self._set(V, False)
        return value

    def _acc_neg(self, mode, reg):
        self._modify(mode, self._f_neg, reg)

    def _acc_com(self, mode, reg):
        self._modify(mode, self._f_com, reg)

    def _acc_lsr(self, mode, reg):
        self._modify(mode, self._f_lsr, reg)

    def _acc_ror(self, mode, reg):
        self._modify(mode, self._f_ror, reg)

    def _acc_asr(self, mode, reg):
        self._modify(mode, self._f_asr, reg)

    def _acc_lsl(self, mode, reg):
        self._modify(mode, self._f_lsl, reg)

    def _acc_rol(self, mode, reg):
        self._modify(mode, self._f_rol, reg)

    def _acc_dec(self, mode, reg):
        self._modify(mode, self._f_dec, reg)

    def _acc_inc(self, mode, reg):
        self._modify(mode, self._f_inc, reg)

    def _acc_clr(self, mode, reg):
        self._modify(mode, self._f_clr, reg)

    def _acc_tst(self, mode, reg):
        self._modify(mode, self._f_tst, reg)

    def _op_neg(self, mode):
        self._modify(mode, self._f_neg)

    def _op_com(self, mode):
        self._modify(mode, self._f_com)

    def _op_lsr(self, mode):
        self._modify(mode, self._f_lsr)

    def _op_ror(self, mode):
        self._modify(mode, self._f_ror)

    def _op_asr(self, mode):
        self._modify(mode, self._f_asr)

    def _op_lsl(self, mode):
        self._modify(mode, self._f_lsl)

    def _op_rol(self, mode):
        self._modify(mode, self._f_rol)

    def _op_dec(self, mode):
        self._modify(mode, self._f_dec)

    def _op_inc(self, mode):
        self._modify(mode, self._f_inc)

    def _op_clr(self, mode):
        self._modify(mode, self._f_clr)

    def _op_tst(self, mode):
        self._modify(mode, self._f_tst)

    # -- flow -------------------------------------------------------

    _CONDITIONS = {
        "BRA": lambda cc: True,
        "BRN": lambda cc: False,
        "BHI": lambda cc: not (cc & C) and not (cc & Z),
        "BLS": lambda cc: (cc & C) or (cc & Z),
        "BCC": lambda cc: not (cc & C),
        "BCS": lambda cc: cc & C,
        "BNE": lambda cc: not (cc & Z),
        "BEQ": lambda cc: cc & Z,
        "BVC": lambda cc: not (cc & V),
        "BVS": lambda cc: cc & V,
        "BPL": lambda cc: not (cc & N),
        "BMI": lambda cc: cc & N,
        "BGE": lambda cc: bool(cc & N) == bool(cc & V),
        "BLT": lambda cc: bool(cc & N) != bool(cc & V),
        "BGT": lambda cc: not (cc & Z) and bool(cc & N) == bool(cc & V),
        "BLE": lambda cc: (cc & Z) or bool(cc & N) != bool(cc & V),
    }

    def _branch(self, mnemonic, mode):
        if mode is RELATIVE:
            offset = self._signed(self._fetch(), 8)
        else:
            offset = self._signed(self._fetch16(), 16)
        if self._CONDITIONS[mnemonic](self.cc):
            self.pc = (self.pc + offset) & 0xFFFF

    def _op_jmp(self, mode):
        self.pc = self._address(mode)

    def _op_jsr(self, mode):
        address = self._address(mode)
        self._push16("s", self.pc)
        self.pc = address

    def _op_bsr(self, mode):
        offset = self._signed(self._fetch(), 8)
        self._push16("s", self.pc)
        self.pc = (self.pc + offset) & 0xFFFF

    def _op_lbsr(self, mode):
        offset = self._signed(self._fetch16(), 16)
        self._push16("s", self.pc)
        self.pc = (self.pc + offset) & 0xFFFF

    def _op_rts(self, mode):
        self.pc = self._pull16("s")

    def _op_rti(self, mode):
        self.cc = self._pull8("s")
        if self.cc & E:
            self._pull_set("s", 0xFE)
        else:
            self.pc = self._pull16("s")

    def _op_swi(self, mode):
        self._enter(0xFFFA, True)
        self.cc |= F | I

    def _op_swi2(self, mode):
        self._enter(0xFFF4, True)

    def _op_swi3(self, mode):
        self._enter(0xFFF2, True)

    def _op_sync(self, mode):
        self.waiting = True

    def _op_cwai(self, mode):
        self.cc &= self._fetch()
        self._set(E, True)
        self._push_set("s", 0xFF)
        self.waiting = True

    def _op_nop(self, mode):
        pass

    # -- stack and transfer -----------------------------------------

    def _op_pshs(self, mode):
        self._push_set("s", self._fetch())

    def _op_puls(self, mode):
        self._pull_set("s", self._fetch())

    def _op_pshu(self, mode):
        self._push_set("u", self._fetch())

    def _op_pulu(self, mode):
        self._pull_set("u", self._fetch())

    def _registers(self, nibble):
        if nibble in _PAIR16:
            return _PAIR16[nibble], True
        if nibble in _PAIR8:
            return _PAIR8[nibble], False
        raise ValueError("registro invalido en postbyte: $%X" % nibble)

    def _op_tfr(self, mode):
        postbyte = self._fetch()
        source, wide = self._registers(postbyte >> 4)
        target, _ = self._registers(postbyte & 0x0F)
        setattr(self, target, getattr(self, source))

    def _op_exg(self, mode):
        postbyte = self._fetch()
        first, _ = self._registers(postbyte >> 4)
        second, _ = self._registers(postbyte & 0x0F)
        a, b = getattr(self, first), getattr(self, second)
        setattr(self, first, b)
        setattr(self, second, a)


def _install_branches():
    """Binds every branch mnemonic, short and long, to one handler."""
    for name in list(Cpu._CONDITIONS):
        for mnemonic in (name, "L" + name):
            setattr(Cpu, "_op_" + mnemonic.lower(),
                    (lambda m: lambda self, mode: self._branch(m, mode))(name))
    # LBRA and LBSR have their own page 1 opcodes; LBRA is just a branch.
    Cpu._op_lbra = lambda self, mode: self._branch("BRA", mode)


_install_branches()
