"""Spec for the MC6809 core that runs the sound board's program.

The point of emulating the CPU is to stop guessing at the sound script
VM: run U18's own code and watch what it writes to the YM2151. So the
bar here is behavioural fidelity on the instructions that program
actually uses, checked against the real ROM at the end.
"""

import pytest

from wpc6809.cpu import Cpu, Memory


def _cpu(program, origin=0x1000, **registers):
    memory = Memory(bytearray(0x10000))
    memory.data[origin:origin + len(program)] = program
    cpu = Cpu(memory)
    cpu.pc = origin
    for name, value in registers.items():
        setattr(cpu, name, value)
    return cpu


# -- registers and flags ---------------------------------------------


def test_reset_loads_the_pc_from_the_vector():
    memory = Memory(bytearray(0x10000))
    memory.data[0xFFFE:0x10000] = b"\xdd\xdd"
    cpu = Cpu(memory)

    cpu.reset()

    assert cpu.pc == 0xDDDD
    # Reset masks both interrupts and clears the direct page.
    assert cpu.cc & 0x50 == 0x50
    assert cpu.dp == 0


def test_immediate_load_sets_the_negative_flag():
    cpu = _cpu(b"\x86\x80")           # LDA #$80
    cpu.step()
    assert cpu.a == 0x80
    assert cpu.cc & 0x08             # N


def test_immediate_load_sets_the_zero_flag():
    cpu = _cpu(b"\x86\x00")           # LDA #$00
    cpu.step()
    assert cpu.cc & 0x04             # Z


def test_d_is_a_and_b_together():
    cpu = _cpu(b"\xcc\x12\x34")       # LDD #$1234
    cpu.step()
    assert (cpu.a, cpu.b) == (0x12, 0x34)
    assert cpu.d == 0x1234


# -- addressing modes ------------------------------------------------


def test_direct_addressing_uses_the_dp_register():
    cpu = _cpu(b"\x96\x40", dp=0x02)  # LDA $40, DP=$02 -> $0240
    cpu.memory.data[0x0240] = 0x5A
    cpu.step()
    assert cpu.a == 0x5A


def test_extended_addressing_reads_a_full_address():
    cpu = _cpu(b"\xb6\x30\x00")       # LDA $3000
    cpu.memory.data[0x3000] = 0x99
    cpu.step()
    assert cpu.a == 0x99


def test_indexed_auto_increment_advances_the_register():
    cpu = _cpu(b"\xa6\x80", x=0x2000)  # LDA ,X+
    cpu.memory.data[0x2000] = 0x42
    cpu.step()
    assert (cpu.a, cpu.x) == (0x42, 0x2001)


def test_indexed_five_bit_offset_is_signed():
    cpu = _cpu(b"\xa6\x1f", x=0x2000)  # LDA -1,X
    cpu.memory.data[0x1FFF] = 0x77
    cpu.step()
    assert cpu.a == 0x77


def test_indexed_indirect_follows_the_pointer():
    cpu = _cpu(b"\xa6\x94", x=0x2000)  # LDA [,X]
    cpu.memory.data[0x2000:0x2002] = b"\x30\x00"
    cpu.memory.data[0x3000] = 0x55
    cpu.step()
    assert cpu.a == 0x55


# -- arithmetic ------------------------------------------------------


def test_addition_carries():
    cpu = _cpu(b"\x8b\x01", a=0xFF)   # ADDA #$01
    cpu.step()
    assert cpu.a == 0x00
    assert cpu.cc & 0x01             # C


def test_subtraction_borrows_and_sets_carry():
    cpu = _cpu(b"\x80\x01", a=0x00)   # SUBA #$01
    cpu.step()
    assert cpu.a == 0xFF
    assert cpu.cc & 0x01


def test_multiply_puts_the_product_in_d():
    cpu = _cpu(b"\x3d", a=0x10, b=0x10)   # MUL
    cpu.step()
    assert cpu.d == 0x0100


def test_sixteen_bit_subtraction():
    cpu = _cpu(b"\x83\x00\x01", a=0x00, b=0x00)   # SUBD #$0001
    cpu.step()
    assert cpu.d == 0xFFFF


# -- flow ------------------------------------------------------------


def test_branch_not_equal_is_taken_when_z_is_clear():
    cpu = _cpu(b"\x26\x10")           # BNE +16
    cpu.cc &= ~0x04
    cpu.step()
    assert cpu.pc == 0x1012


def test_branch_not_equal_falls_through_when_z_is_set():
    cpu = _cpu(b"\x26\x10")
    cpu.cc |= 0x04
    cpu.step()
    assert cpu.pc == 0x1002


def test_jump_to_subroutine_and_return():
    cpu = _cpu(b"\xbd\x20\x00", s=0x1FFF)     # JSR $2000
    cpu.memory.data[0x2000] = 0x39            # RTS
    cpu.step()
    assert cpu.pc == 0x2000
    cpu.step()
    assert cpu.pc == 0x1003


def test_push_and_pull_restore_a_register():
    cpu = _cpu(b"\x34\x10\x35\x10", x=0x1234, s=0x1FFF)   # PSHS X / PULS X
    cpu.step()
    cpu.x = 0
    cpu.step()
    assert cpu.x == 0x1234


def test_transfer_copies_between_registers():
    cpu = _cpu(b"\x1f\x89", a=0x42)   # TFR A,B
    cpu.step()
    assert cpu.b == 0x42


# -- interrupts ------------------------------------------------------


def test_a_firq_stacks_little_and_vectors():
    memory = Memory(bytearray(0x10000))
    memory.data[0xFFF6:0xFFF8] = b"\xdd\x25"
    cpu = Cpu(memory)
    cpu.pc, cpu.s, cpu.cc = 0x1000, 0x1FFF, 0x00

    cpu.firq()

    assert cpu.pc == 0xDD25
    assert cpu.cc & 0x50 == 0x50, "FIRQ masks both interrupts"
    assert cpu.s == 0x1FFC, "FIRQ stacks only PC and CC"


def test_a_masked_firq_is_ignored():
    memory = Memory(bytearray(0x10000))
    memory.data[0xFFF6:0xFFF8] = b"\xdd\x25"
    cpu = Cpu(memory)
    cpu.pc, cpu.s, cpu.cc = 0x1000, 0x1FFF, 0x40    # F set

    cpu.firq()

    assert cpu.pc == 0x1000


def test_an_irq_stacks_the_whole_state():
    memory = Memory(bytearray(0x10000))
    memory.data[0xFFF8:0xFFFA] = b"\xdd\xcb"
    cpu = Cpu(memory)
    cpu.pc, cpu.s, cpu.cc = 0x1000, 0x1FFF, 0x00

    cpu.irq()

    assert cpu.pc == 0xDDCB
    assert cpu.s == 0x1FF3, "IRQ stacks twelve bytes: PC, U, Y, X, DP, B, A, CC"
    assert cpu.cc & 0x80, "the E flag records the full stack frame"


# -- against the real program ----------------------------------------


def test_cycles_are_counted():
    cpu = _cpu(b"\x12")               # NOP
    before = cpu.cycles
    cpu.step()
    assert cpu.cycles > before
