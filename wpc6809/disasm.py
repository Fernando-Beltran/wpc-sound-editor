"""A disassembler for the MC6809, the CPU on a WPC sound board.

Written for one job: reading the 16 KB fixed bank at the tail of U18
(ROM `0x07C000`-`0x07FFFF`, mapped to CPU `$C000`-`$FFFF`) to find the
sound command dispatch table and the sample pointer tables behind it.
That is what lets a sound ROM be edited on its own, with the game ROM
untouched. Ghidra ships no MC6809 module and the emulator-driven route
needs a debugger we cannot drive, so the listing has to come from here.

The 6809's encoding is regular enough to table-drive. One opcode byte
selects both operation and addressing mode, except that `0x10` and
`0x11` are prefixes into two sparse second pages, and indexed mode
hides its real shape in a postbyte that can pull in two more bytes.
Anything the chip leaves undefined comes back as `FCB`, since in a
sound ROM an undefined opcode usually means the listing has wandered
into sample data.
"""

from collections import namedtuple

# `data` keeps the raw bytes for a listing column; `target` carries the
# absolute address a branch, jump or extended operand refers to, which
# is what lets a later pass follow the dispatch table's entries.
Instruction = namedtuple(
    "Instruction", "address mnemonic operand length data target", defaults=(b"", None)
)

# Addressing modes, as the tables below name them.
INHERENT = "inherent"
IMMEDIATE = "immediate"
DIRECT = "direct"
EXTENDED = "extended"
INDEXED = "indexed"
RELATIVE = "relative"
LONG_RELATIVE = "long relative"
REGISTER_LIST = "register list"
REGISTER_PAIR = "register pair"

# The operations that take a 16-bit immediate operand; every other
# immediate instruction takes a byte. Cheaper than a width column.
_WORD_IMMEDIATE = frozenset(
    ["SUBD", "ADDD", "CMPD", "CMPX", "CMPY", "CMPU", "CMPS",
     "LDD", "LDX", "LDY", "LDU", "LDS"]
)

# The read-modify-write block that appears three times over, once per
# memory addressing mode (0x00 direct, 0x60 indexed, 0x70 extended),
# and again at 0x40/0x50 as inherent operations on A and B.
_MEMORY_OPS = [
    "NEG", None, None, "COM", "LSR", None, "ROR", "ASR",
    "LSL", "ROL", "DEC", None, "INC", "TST", "JMP", "CLR",
]

# The accumulator blocks. A-column at 0x80/0x90/0xA0/0xB0, B-column at
# 0xC0/0xD0/0xE0/0xF0. Store/immediate combinations do not exist, and
# the immediate slot of JSR is BSR instead; both handled below.
_ACC_A_OPS = [
    "SUBA", "CMPA", "SBCA", "SUBD", "ANDA", "BITA", "LDA", "STA",
    "EORA", "ADCA", "ORA", "ADDA", "CMPX", "JSR", "LDX", "STX",
]
_ACC_B_OPS = [
    "SUBB", "CMPB", "SBCB", "ADDD", "ANDB", "BITB", "LDB", "STB",
    "EORB", "ADCB", "ORB", "ADDB", "LDD", "STD", "LDU", "STU",
]

_SHORT_BRANCHES = [
    "BRA", "BRN", "BHI", "BLS", "BCC", "BCS", "BNE", "BEQ",
    "BVC", "BVS", "BPL", "BMI", "BGE", "BLT", "BGT", "BLE",
]


def _build_page1():
    table = [None] * 256

    for low, mnemonic in enumerate(_MEMORY_OPS):
        if mnemonic is None:
            continue
        table[0x00 + low] = (mnemonic, DIRECT)
        table[0x60 + low] = (mnemonic, INDEXED)
        table[0x70 + low] = (mnemonic, EXTENDED)
        # JMP is the only one of these with no register form.
        if mnemonic != "JMP":
            table[0x40 + low] = (mnemonic + "A", INHERENT)
            table[0x50 + low] = (mnemonic + "B", INHERENT)

    for low, mnemonic in enumerate(_SHORT_BRANCHES):
        table[0x20 + low] = (mnemonic, RELATIVE)

    for low, (a_op, b_op) in enumerate(zip(_ACC_A_OPS, _ACC_B_OPS)):
        table[0x90 + low] = (a_op, DIRECT)
        table[0xA0 + low] = (a_op, INDEXED)
        table[0xB0 + low] = (a_op, EXTENDED)
        table[0xD0 + low] = (b_op, DIRECT)
        table[0xE0 + low] = (b_op, INDEXED)
        table[0xF0 + low] = (b_op, EXTENDED)
        # Nothing stores an immediate, and 0x8D is BSR, not immediate JSR.
        if not a_op.startswith("ST") and a_op != "JSR":
            table[0x80 + low] = (a_op, IMMEDIATE)
        if not b_op.startswith("ST"):
            table[0xC0 + low] = (b_op, IMMEDIATE)

    table[0x12] = ("NOP", INHERENT)
    table[0x13] = ("SYNC", INHERENT)
    table[0x16] = ("LBRA", LONG_RELATIVE)
    table[0x17] = ("LBSR", LONG_RELATIVE)
    table[0x19] = ("DAA", INHERENT)
    table[0x1A] = ("ORCC", IMMEDIATE)
    table[0x1C] = ("ANDCC", IMMEDIATE)
    table[0x1D] = ("SEX", INHERENT)
    table[0x1E] = ("EXG", REGISTER_PAIR)
    table[0x1F] = ("TFR", REGISTER_PAIR)

    table[0x30] = ("LEAX", INDEXED)
    table[0x31] = ("LEAY", INDEXED)
    table[0x32] = ("LEAS", INDEXED)
    table[0x33] = ("LEAU", INDEXED)
    table[0x34] = ("PSHS", REGISTER_LIST)
    table[0x35] = ("PULS", REGISTER_LIST)
    table[0x36] = ("PSHU", REGISTER_LIST)
    table[0x37] = ("PULU", REGISTER_LIST)
    table[0x39] = ("RTS", INHERENT)
    table[0x3A] = ("ABX", INHERENT)
    table[0x3B] = ("RTI", INHERENT)
    table[0x3C] = ("CWAI", IMMEDIATE)
    table[0x3D] = ("MUL", INHERENT)
    table[0x3F] = ("SWI", INHERENT)

    table[0x8D] = ("BSR", RELATIVE)

    return table


def _build_page2():
    table = {0x3F: ("SWI2", INHERENT)}

    # The long branches mirror the short ones from BRN on; there is no
    # long BRA here because it already has its own opcode at 0x16.
    for low, mnemonic in enumerate(_SHORT_BRANCHES):
        if low == 0:
            continue
        table[0x20 + low] = ("L" + mnemonic, LONG_RELATIVE)

    for base, mode in ((0x80, IMMEDIATE), (0x90, DIRECT),
                       (0xA0, INDEXED), (0xB0, EXTENDED)):
        table[base + 0x3] = ("CMPD", mode)
        table[base + 0xC] = ("CMPY", mode)
        table[base + 0xE] = ("LDY", mode)
        if mode is not IMMEDIATE:
            table[base + 0xF] = ("STY", mode)

    for base, mode in ((0xC0, IMMEDIATE), (0xD0, DIRECT),
                       (0xE0, INDEXED), (0xF0, EXTENDED)):
        table[base + 0xE] = ("LDS", mode)
        if mode is not IMMEDIATE:
            table[base + 0xF] = ("STS", mode)

    return table


def _build_page3():
    table = {0x3F: ("SWI3", INHERENT)}
    for base, mode in ((0x80, IMMEDIATE), (0x90, DIRECT),
                       (0xA0, INDEXED), (0xB0, EXTENDED)):
        table[base + 0x3] = ("CMPU", mode)
        table[base + 0xC] = ("CMPS", mode)
    return table


PAGE1 = _build_page1()
PAGE2 = _build_page2()
PAGE3 = _build_page3()

_INDEX_REGISTERS = ("X", "Y", "U", "S")

# Register codes shared by TFR and EXG; the gaps are undefined.
_PAIR_REGISTERS = {
    0x0: "D", 0x1: "X", 0x2: "Y", 0x3: "U", 0x4: "S", 0x5: "PC",
    0x8: "A", 0x9: "B", 0xA: "CC", 0xB: "DP",
}

# Stack postbyte, low bit first. Bit 6 names the *other* stack pointer,
# so it depends on which instruction we are in.
_STACK_REGISTERS = ("CC", "A", "B", "DP", "X", "Y", None, "PC")


class _Truncated(Exception):
    """An instruction's operand bytes run past the end of the data."""


def _signed_hex(value):
    """Formats a signed offset the way a 6809 listing reads."""
    if value < 0:
        return "-$%02X" % -value
    return "$%02X" % value


def _signed(value, bits):
    sign = 1 << (bits - 1)
    return value - (1 << bits) if value & sign else value


class _Reader:
    """Walks the data, refusing to read past its end."""

    def __init__(self, data, position):
        self._data = data
        self.position = position

    def byte(self):
        if self.position >= len(self._data):
            raise _Truncated()
        value = self._data[self.position]
        self.position += 1
        return value

    def word(self):
        return (self.byte() << 8) | self.byte()


def _decode_indexed(reader):
    """Decodes an indexed postbyte into an operand string.

    Returns the operand and, for the extended-indirect form, the
    absolute address it names. A PC-relative offset counts from the end
    of the whole instruction, whose length is not known yet, so that
    one comes back as a callable for the caller to finish.
    """
    postbyte = reader.byte()
    register = _INDEX_REGISTERS[(postbyte >> 5) & 0x3]

    # Bit 7 clear is the compact form: a 5-bit signed offset, and no
    # indirection available.
    if not postbyte & 0x80:
        return "%s,%s" % (_signed_hex(_signed(postbyte & 0x1F, 5)), register), None

    indirect = bool(postbyte & 0x10)
    mode = postbyte & 0x0F
    target = None

    if mode == 0x0:
        operand = ",%s+" % register
    elif mode == 0x1:
        operand = ",%s++" % register
    elif mode == 0x2:
        operand = ",-%s" % register
    elif mode == 0x3:
        operand = ",--%s" % register
    elif mode == 0x4:
        operand = ",%s" % register
    elif mode == 0x5:
        operand = "B,%s" % register
    elif mode == 0x6:
        operand = "A,%s" % register
    elif mode == 0x8:
        operand = "%s,%s" % (_signed_hex(_signed(reader.byte(), 8)), register)
    elif mode == 0x9:
        operand = "%s,%s" % (_signed_hex(_signed(reader.word(), 16)), register)
    elif mode == 0xB:
        operand = "D,%s" % register
    elif mode in (0xC, 0xD):
        offset = _signed(reader.byte(), 8) if mode == 0xC else _signed(reader.word(), 16)
        operand = "%s,PCR" % _signed_hex(offset)
        target = lambda end, offset=offset: (end + offset) & 0xFFFF
    elif mode == 0xF and indirect:
        target = reader.word()
        operand = "$%04X" % target
    else:
        # Undefined postbyte. Say so rather than inventing an operand.
        return "<$%02X>" % postbyte, None

    if indirect:
        operand = "[%s]" % operand
    return operand, target


def _decode_register_list(reader, other_stack):
    postbyte = reader.byte()
    names = [
        other_stack if index == 6 else name
        for index, name in enumerate(_STACK_REGISTERS)
        if postbyte & (1 << index)
    ]
    return ",".join(names)


def _decode_one(data, position, origin):
    """Decodes the instruction at `position`, or raises _Truncated.

    Returns None where the chip has nothing defined, which the caller
    turns into an `FCB` byte.
    """
    reader = _Reader(data, position)
    opcode = reader.byte()

    if opcode == 0x10:
        entry = PAGE2.get(reader.byte())
    elif opcode == 0x11:
        entry = PAGE3.get(reader.byte())
    else:
        entry = PAGE1[opcode]

    if entry is None:
        return None

    mnemonic, mode = entry
    target = None

    if mode is INHERENT:
        operand = ""
    elif mode is IMMEDIATE:
        if mnemonic in _WORD_IMMEDIATE:
            operand = "#$%04X" % reader.word()
        else:
            operand = "#$%02X" % reader.byte()
    elif mode is DIRECT:
        operand = "$%02X" % reader.byte()
    elif mode is EXTENDED:
        target = reader.word()
        operand = "$%04X" % target
    elif mode is INDEXED:
        operand, target = _decode_indexed(reader)
    elif mode in (RELATIVE, LONG_RELATIVE):
        if mode is RELATIVE:
            displacement = _signed(reader.byte(), 8)
        else:
            displacement = _signed(reader.word(), 16)
        # The displacement counts from the end of the instruction.
        target = (origin + (reader.position - position) + displacement) & 0xFFFF
        operand = "$%04X" % target
    elif mode is REGISTER_LIST:
        other_stack = "U" if mnemonic.endswith("S") else "S"
        operand = _decode_register_list(reader, other_stack)
    elif mode is REGISTER_PAIR:
        postbyte = reader.byte()
        source = _PAIR_REGISTERS.get(postbyte >> 4)
        destination = _PAIR_REGISTERS.get(postbyte & 0x0F)
        if source is None or destination is None:
            return None
        operand = "%s,%s" % (source, destination)
    else:
        raise AssertionError("modo de direccionamiento sin decodificar: %s" % mode)

    length = reader.position - position
    if callable(target):
        # A PC-relative indexed offset, now that the length is known.
        target = target(origin + length)

    return Instruction(
        address=origin,
        mnemonic=mnemonic,
        operand=operand,
        length=length,
        data=bytes(data[position:reader.position]),
        target=target,
    )


def disassemble(data, origin=0, start=0, end=None):
    """Disassembles `data` linearly, as if it were all code.

    `origin` is the CPU address of `data[0]`, so a bank read out of the
    ROM file disassembles at its mapped address and `start`/`end` stay
    plain offsets into the bank. Bytes that decode to nothing -- an
    undefined opcode, or an operand cut off by the end of the data --
    come back as a one-byte `FCB`, which is also the honest reading
    when a linear sweep walks off code and into sample data.
    """
    if end is None:
        end = len(data)

    instructions = []
    position = start
    while position < end:
        address = (origin + position) & 0xFFFF
        try:
            instruction = _decode_one(data, position, address)
        except _Truncated:
            instruction = None

        if instruction is None:
            instruction = Instruction(
                address=address,
                mnemonic="FCB",
                operand="$%02X" % data[position],
                length=1,
                data=bytes(data[position:position + 1]),
            )

        instructions.append(instruction)
        position += instruction.length

    return instructions


def format_listing(instructions):
    """Renders instructions as `address: bytes  mnemonic operand` lines."""
    lines = []
    for instruction in instructions:
        raw = " ".join("%02X" % byte for byte in instruction.data)
        text = instruction.mnemonic
        if instruction.operand:
            text += " " + instruction.operand
        lines.append("%04X: %-14s %s" % (instruction.address, raw, text))
    return "\n".join(lines)
