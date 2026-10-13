"""The WPC pre-DCS sound board, enough of it to run its own program.

Memory map and banking are PinMAME's (`src/wpc/wmssnd.c`), checked
against the disassembly of U18:

    $0000-$1FFF  RAM
    $2000        ROM bank select (write)
    $2400/$2401  YM2151 address / data; reading $2401 is its status
    $2800        8-bit DAC data
    $2C00        HC55516 clock
    $3000        command latch from the main CPU (read)
    $3400        HC55516 data
    $3800        volume
    $3C00        reply latch back to the main CPU
    $4000-$BFFF  32 KB banked ROM window
    $C000-$FFFF  fixed 16 KB, the top page of U18

The YM2151 here is not a synthesiser: it keeps its registers, models
Timer A well enough to raise the board's FIRQ, and above all **records
every register write with the cycle it happened on**. That log is the
music -- on this board music is not recorded audio but a stream of FM
register writes -- and it is what `wpcaudio.vgm` turns into a file a
chiptune player can render.
"""

from collections import namedtuple

from wpc6809.cpu import Cpu, Memory
from wpcaudio.soundrom import CHIP_SELECT, WINDOW_BASE, WINDOW_SIZE

# The board's two clocks. The 6809 runs at 2 MHz; the YM2151 has its own
# 3.579545 MHz crystal, which is what its timer periods are counted in.
CPU_CLOCK = 2000000
YM_CLOCK = 3579545

# Timer A ticks every 64 YM clocks and counts up to 1024.
TIMER_A_TICK = 64
TIMER_A_LIMIT = 1024

# How many YM2151 clocks pass per 6809 cycle.
YM_PER_CPU_CYCLE = YM_CLOCK / float(CPU_CLOCK)

# Resetting the timer flag does not drop the chip's IRQ line at once: it
# falls about 3 microseconds later. PinMAME models exactly this
# (`IRQ_CLEAR_DELAY_TIME_US` in its ym2151.c) as the real chip's
# propagation delay, and notes that WPC89 sound software depends on it.
# This program does: its main loop masks interrupts, writes $14 = $15
# (which resets the flag) and unmasks a few cycles later. With the line
# dropped instantly, every overflow that lands in that window is lost --
# 8% of FIRQs with FM playing, 32% with the DAC streaming, which slowed
# both the sample clock and the script tempo. With the delay the line is
# still up when the mask lifts, and the interrupt is taken.
IRQ_CLEAR_DELAY = 3e-6 * YM_CLOCK

# Twilight Zone's U18: where the startup delay loop sits, and the main
# loop the reset path jumps to when it leaves that loop early.
TZ_DELAY_LOOP = 0xDE1F
TZ_MAIN_LOOP = 0xDF24

RegisterWrite = namedtuple("RegisterWrite", "cycle register value")
SampleStart = namedtuple("SampleStart", "cycle stream bank start end")


class Ym2151:
    """Registers, Timer A and a log. No synthesis."""

    def __init__(self):
        self.registers = bytearray(256)
        self.address = 0
        self.writes = []
        self.irq = False
        # YM clocks left before a reset flag actually drops the line.
        self._clearing = None
        self._clear_fresh = False
        self._timer_a = 0.0
        self._enabled = False
        self._loaded = False
        self._status = 0

    @property
    def period(self):
        """Timer A's period in YM clocks, from registers $10 and $11."""
        count = (self.registers[0x10] << 2) | (self.registers[0x11] & 0x03)
        return (TIMER_A_LIMIT - count) * TIMER_A_TICK

    def write(self, port, value, cycle):
        if port == 0:
            self.address = value
            return
        self.registers[self.address] = value
        self.writes.append(RegisterWrite(cycle, self.address, value))

        if self.address == 0x14:
            # Bit 0 loads Timer A, bit 2 enables its interrupt, bit 4
            # clears the overflow flag and drops the interrupt line.
            self._enabled = bool(value & 0x04)
            if value & 0x10 and self._status & 0x01:
                self._status &= ~0x01
                self._clearing = IRQ_CLEAR_DELAY
                self._clear_fresh = True
            # LOAD is edge sensitive. Writing it again while the timer
            # is already running does nothing -- and this program
            # rewrites $14 on every pass of its main loop, so treating
            # it as a restart pins the count at zero and the interrupt
            # never arrives.
            loaded = bool(value & 0x01)
            if loaded and not self._loaded:
                self._timer_a = 0.0
            self._loaded = loaded

    def read_status(self):
        # Bit 7 is "busy". The program polls it in tight loops, and a
        # chip that is never busy simply lets them through.
        return self._status & 0x03

    def advance(self, ym_clocks):
        """Runs Timer A forward; returns True if it just overflowed."""
        if self._clearing is not None:
            # The write lands on the last cycle of its instruction, so
            # that instruction's own cycles do not count toward the delay.
            if self._clear_fresh:
                self._clear_fresh = False
            else:
                self._clearing -= ym_clocks
            if self._clearing <= 0:
                self._clearing = None
                self.irq = False
        if not self._enabled:
            return False
        self._timer_a += ym_clocks
        period = self.period
        if self._timer_a < period:
            return False
        self._timer_a -= period
        self._status |= 0x01
        self.irq = True
        # A fresh overflow cancels a clear still on its way.
        self._clearing = None
        return True


class SoundBoardMemory(Memory):
    """The 6809's view of the board."""

    def __init__(self, board):
        super().__init__(bytearray(0x2000))
        self.board = board

    def read(self, address):
        address &= 0xFFFF
        if address < 0x2000:
            return self.data[address]
        if address < 0x4000:
            if 0x2400 <= address < 0x2800 and address & 1:
                return self.board.ym.read_status()
            if 0x3000 <= address < 0x3400:
                return self.board.take_command()
            return 0
        if address < 0xC000:
            return self.board.banked(address)
        return self.board.fixed[address - 0xC000]

    def write(self, address, value):
        address &= 0xFFFF
        value &= 0xFF
        if address < 0x2000:
            self.data[address] = value
            self.board.note_write(address, value)
        elif address < 0x2400:
            self.board.bank = value
        elif address < 0x2800:
            self.board.ym.write(address & 1, value, self.board.cpu.cycles)
        elif address < 0x2C00:
            self.board.dac_writes += 1
        elif address < 0x3000:
            self.board.cvsd_clocks += 1
        elif address < 0x3800:
            pass                      # HC55516 data, and the unused $3000 block
        elif address < 0x3C00:
            self.board.volume = value
        elif address < 0x4000:
            self.board.replies.append(value)


class SoundBoard:
    """A sound ROM set with a 6809 running its program."""

    def __init__(self, rom):
        self.rom = rom
        self.ym = Ym2151()
        self.bank = 0x7C
        self.volume = 0
        self.dac_writes = 0
        self.cvsd_clocks = 0
        self.replies = []
        self.commands = []
        self.samples = []
        self.fixed = bytes(rom.chips["u18"][-0x4000:])
        self.memory = SoundBoardMemory(self)
        self.cpu = Cpu(self.memory)
        self.cpu.reset()
        self._arming = None

    # -- the bank window --------------------------------------------

    def banked(self, address):
        selected = CHIP_SELECT.get(self.bank & 0xE0)
        if selected is None:
            return 0xFF
        chip = selected[0]
        image = self.rom.chips.get(chip)
        if image is None:
            return 0xFF
        offset = ((self.bank & 0x0F) << 15) + (address - WINDOW_BASE)
        return image[offset % len(image)]

    # -- the command latch ------------------------------------------

    def send(self, command):
        """Queues a command byte, as the main CPU would."""
        self.commands.append(command & 0xFF)

    def take_command(self):
        return self.commands.pop(0) if self.commands else 0xFF

    # -- running -----------------------------------------------------

    def note_write(self, address, value):
        """Watches the program start a sample stream.

        Polling the direct page cannot do this: the FIRQ rewrites the
        CVSD pointer on every byte it consumes, so the variables are in
        motion constantly. What is unambiguous is the **order the
        handlers write them in**, so each stream is caught on the one
        write that completes its set.

        DAC (`$E600`): `STD $34` (end - 1), `LDY $01,X` (start), then
        `STA $2C` (bank). By the time the bank lands, `Y` holds the
        start and `$34` the end less one -- that subtraction is why
        matching `$34` straight against the table never hit.

        CVSD (`$E84A`): `STA $2B` (bank), then `STD $43` (end), then
        `STY $41` (pointer, already advanced one byte by `LDB ,Y+`).
        The bank arms the watch and the pointer completes it, which is
        what keeps the FIRQ's own `STX $41` out of the results.
        """
        page = self.cpu.dp << 8
        ram = self.memory.data

        if address == page + 0x2C:
            if value:
                end = ((ram[page + 0x34] << 8) | ram[page + 0x35]) + 1
                self.samples.append(SampleStart(self.cpu.cycles, "dac", value,
                                                self.cpu.y, end & 0xFFFF))
            return

        if address == page + 0x2B:
            self._arming = value or None
            return

        if address == page + 0x42 and self._arming:
            start = (((ram[page + 0x41] << 8) | value) - 1) & 0xFFFF
            end = (ram[page + 0x43] << 8) | ram[page + 0x44]
            self.samples.append(SampleStart(self.cpu.cycles, "cvsd",
                                            self._arming, start, end))
            self._arming = None

    def _tick(self):
        """One instruction, with the timer and interrupts serviced.

        The timer has to be advanced and the interrupt offered after
        **every** instruction, not every few hundred cycles. The main
        loop writes `$14 = $15` on every pass, and bit 4 of that value
        clears the timer's overflow flag, so a coarse poll loses the
        interrupt: the program wipes the flag before the emulator ever
        looks at it. Polling every 50 instructions dropped the FIRQ
        rate from 5593 Hz to two interrupts a second.
        """
        used = self.cpu.step()
        self.ym.advance(used * YM_PER_CPU_CYCLE)
        if self.ym.irq:
            self.cpu.firq()
        elif self.commands:
            self.cpu.irq()

    def run(self, seconds, on_progress=None, every=50000):
        """Runs the board for a stretch of emulated time.

        `on_progress(done_seconds, total_seconds)` is called every
        `every` cycles, which is what lets a front end show how far a
        render has got: emulating the board is almost all of the time a
        render takes, and the emulator knows exactly where it is.
        """
        start = self.cpu.cycles
        target = start + int(seconds * CPU_CLOCK)
        next_report = start + every
        while self.cpu.cycles < target:
            self._tick()
            if on_progress is not None and self.cpu.cycles >= next_report:
                next_report = self.cpu.cycles + every
                on_progress((self.cpu.cycles - start) / float(CPU_CLOCK), seconds)

    def boot(self, limit=2000000, delay_loop=TZ_DELAY_LOOP, main_loop=TZ_MAIN_LOOP):
        """Runs the reset path, then skips the power-on self test.

        The reset code initialises RAM, the command queue and the stack,
        then sits in a delay loop before checksumming all 1.1 MB of ROM
        bank by bank. The checksum proves nothing we do not already know
        -- the images are hash-verified on import -- and at a couple of
        million 6809 instructions it is most of the emulator's running
        time. The reset path itself leaves that loop with
        `LDS #$1FFF / JMP $DF24`, so taking the same exit is the
        program's own shortcut, not a hack around it.

        Returns the cycle the main loop was entered on.
        """
        for _ in range(limit):
            self._tick()
            if self.cpu.pc == delay_loop:
                self.cpu.s = 0x1FFF
                self.cpu.pc = main_loop
                return self.cpu.cycles
        raise RuntimeError("el arranque no llego al bucle de espera en %d pasos" % limit)

    @property
    def firq_rate(self):
        """How often the FIRQ fires, in hertz -- the sample clock.

        The board's playback rate is not a guess once the program has
        programmed Timer A: the FIRQ drives the DAC and the CVSD bit
        clock, so this number calibrates both.
        """
        period = self.ym.period
        return YM_CLOCK / float(period) if period else 0.0
