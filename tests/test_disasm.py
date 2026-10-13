from wpc6809.disasm import disassemble


def _one(data, origin=0xC000):
    instructions = disassemble(data, origin=origin)
    assert len(instructions) == 1, "esperaba una instruccion, salieron %d" % len(instructions)
    return instructions[0]


def test_inherent_opcode_is_one_byte():
    nop = _one(b"\x12")
    assert nop.mnemonic == "NOP"
    assert nop.operand == ""
    assert nop.length == 1


def test_return_from_subroutine():
    assert _one(b"\x39").mnemonic == "RTS"


def test_immediate_byte_operand():
    lda = _one(b"\x86\x42")
    assert (lda.mnemonic, lda.operand) == ("LDA", "#$42")
    assert lda.length == 2


def test_extended_address_operand_is_big_endian():
    jsr = _one(b"\xbd\xc1\x23")
    assert (jsr.mnemonic, jsr.operand) == ("JSR", "$C123")
    assert jsr.length == 3


def test_relative_branch_resolves_to_a_target_address():
    # The 6809 counts the displacement from the end of the instruction:
    # 0xC000 + 2 + 0x7E = 0xC080.
    bra = _one(b"\x20\x7e")
    assert (bra.mnemonic, bra.operand) == ("BRA", "$C080")


def test_relative_branch_displacement_is_signed():
    # 0xC000 + 2 - 2 = 0xC000, a tight loop back onto itself.
    assert _one(b"\x20\xfe").operand == "$C000"


def test_addresses_advance_across_a_sequence():
    instructions = disassemble(b"\x12\x86\x42\x39", origin=0xC000)

    assert [i.address for i in instructions] == [0xC000, 0xC001, 0xC003]
    assert [i.mnemonic for i in instructions] == ["NOP", "LDA", "RTS"]
