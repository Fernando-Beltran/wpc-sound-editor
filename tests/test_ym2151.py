"""Spec for the YM2151 model's Timer A and its interrupt line."""

from wpcaudio.board import IRQ_CLEAR_DELAY, Ym2151


def _running():
    """Timer A loaded with this program's own setting: 640 YM clocks."""
    ym = Ym2151()
    for register, value in ((0x10, 0xFD), (0x11, 0x02), (0x14, 0x15)):
        ym.write(0, register, 0)
        ym.write(1, value, 0)
    return ym


def _reset_flag(ym):
    ym.write(0, 0x14, 0)
    ym.write(1, 0x15, 0)


def test_the_timer_overflows_once_a_period():
    ym = _running()

    overflows = sum(ym.advance(10) for _ in range(64 * 100))

    assert overflows == 100


def test_resetting_the_flag_leaves_the_line_up_for_three_microseconds():
    # The program masks interrupts, resets the flag and unmasks a few
    # cycles later; the line must still be up when it does.
    ym = _running()
    ym.advance(640)
    _reset_flag(ym)

    ym.advance(9)                        # the writing instruction itself
    assert ym.irq
    ym.advance(IRQ_CLEAR_DELAY / 2)
    assert ym.irq
    ym.advance(IRQ_CLEAR_DELAY)
    assert not ym.irq


def test_the_status_flag_clears_at_once():
    ym = _running()
    ym.advance(640)

    _reset_flag(ym)

    assert ym.read_status() & 0x01 == 0


def test_a_new_overflow_cancels_a_pending_clear():
    ym = _running()
    ym.advance(640)
    _reset_flag(ym)
    ym.advance(0)                        # the writing instruction

    ym.advance(639)                      # lands on the next overflow
    ym.advance(IRQ_CLEAR_DELAY * 2)

    assert ym.irq


def test_the_clear_delay_does_not_steal_timer_clocks():
    ym = _running()
    overflows = 0
    for _ in range(100):
        overflows += ym.advance(640)
        _reset_flag(ym)

    assert overflows == 100
