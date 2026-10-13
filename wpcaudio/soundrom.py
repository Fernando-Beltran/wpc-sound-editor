"""The sample directory of a WPC pre-DCS sound ROM set.

Everything here was read off U18's own code with `wpc6809.disasm`, and
the bank arithmetic was then checked against PinMAME's `wmssnd.c`:

- The 6809 sees a **32 KB window at `$4000`-`$BFFF`**, switched by a
  byte written to `$2000`, plus a fixed 16 KB at `$C000`-`$FFFF` that
  is the top page of U18. In that bank byte the top three bits select
  the chip (`$60` U18, `$A0` U15, `$C0` U14) and the low nibble is the
  32 KB page within it. U14 holds 128 KB in a 512 KB slot, so its
  pages wrap -- PinMAME reloads the image four times over.
- A sample is a 5-byte record **`{bank, start, end}`**, addresses being
  window addresses. The handler for sound-script opcode `$08` reads the
  DAC table straight: `LDX $4003` / `LEAX D,X` with `D` the index times
  five, then `LDY $01,X` for the pointer, `LDD $03,X` for the end.
  Opcode `$00` reads the CVSD table through one more indirection: the
  word at `$4015` is a table of 16-bit pointers, each to one record.
- Playback stops on `CMPY $34` for the DAC and on `CMPX $43` for the
  CVSD stream, so `end` is exclusive and a block's size is fixed. A
  replacement sample therefore has to be made to fit, which is what
  `fit_payload` does -- and why it reports what it had to cut or pad.

Both header offsets (`$4003`, `$4015`) and the directory bank (`$7C`)
come from Twilight Zone's U18 and are parameters, not constants: another
game's program ROM will put its tables somewhere else.
"""

from collections import namedtuple

WINDOW_BASE = 0x4000
WINDOW_SIZE = 0x8000

# Chip select, read off `wpcs_rombank_w`: the driver switches on
# `(~data) & 0xE0`, which is these values on the uninverted byte. The
# second number is the chip's first page in the 1.5 MB sound region.
CHIP_SELECT = {0x60: ("u18", 0x00), 0xA0: ("u15", 0x10), 0xC0: ("u14", 0x20)}

# A DAC sample is unsigned and centred, so silence is mid-scale. A CVSD
# stream at rest alternates up and down, which is why 0x55 and 0xAA
# dominate the byte histogram of a real voice region.
DAC_SILENCE = 0x80
CVSD_SILENCE = 0x55

# Twilight Zone's U18: the bank holding the directory, and the window
# offsets of the two table pointers in its header.
TZ_DIRECTORY_BANK = 0x7C
TZ_DAC_TABLE = 0x4003
TZ_CVSD_TABLE = 0x4015

# The power-on self test. The reset path walks a list of bank bytes in
# the fixed bank, ended by $FF, and for each one sums the whole 32 KB
# window: the negated sum, modulo 256, has to equal the bank byte. A page
# that fails is reported as an error on the chip it belongs to, so a
# patched image that does not fix its sums is one the real board
# complains about at power-on.
#
# Every page keeps its correction in its last byte, `$BFFF`: after the
# blocks and the $FF padding comes one byte that differs page to page
# and that no code reads. That is the byte to rewrite.
TZ_CHECKSUM_LIST = 0xDD00
CHECKSUM_BYTE = 0xBFFF
FIXED_PAGE_CORRECTION = 0x7FFF

Block = namedtuple("Block", "index kind bank start end chip offset size")

FitReport = namedtuple(
    "FitReport", "size payload truncated padded capacity_seconds payload_seconds"
)


class SoundRom:
    """A set of sound ROM images, addressed the way the 6809 sees them."""

    def __init__(self, u18, u15=None, u14=None,
                 directory_bank=TZ_DIRECTORY_BANK,
                 dac_table=TZ_DAC_TABLE,
                 cvsd_table=TZ_CVSD_TABLE,
                 checksum_list=TZ_CHECKSUM_LIST):
        self.chips = {"u18": bytearray(u18)}
        if u15 is not None:
            self.chips["u15"] = bytearray(u15)
        if u14 is not None:
            self.chips["u14"] = bytearray(u14)
        self.directory_bank = directory_bank
        self.dac_table = dac_table
        self.cvsd_table = cvsd_table
        self.checksum_list = checksum_list

    # -- addressing -------------------------------------------------

    def resolve(self, bank, address):
        """Turns a bank byte and a window address into (chip, offset)."""
        if not WINDOW_BASE <= address < WINDOW_BASE + WINDOW_SIZE:
            raise ValueError("$%04X no cae en la ventana $4000-$BFFF" % address)

        selected = CHIP_SELECT.get(bank & 0xE0)
        if selected is None:
            raise ValueError("el banco $%02X no selecciona ningun chip" % bank)

        chip, _ = selected
        if chip not in self.chips:
            raise ValueError("falta la imagen de %s" % chip.upper())

        offset = ((bank & 0x0F) << 15) + (address - WINDOW_BASE)
        # A chip smaller than its 512 KB slot is mirrored to fill it.
        return chip, offset % len(self.chips[chip])

    def read(self, bank, address, length):
        chip, offset = self.resolve(bank, address)
        return bytes(self.chips[chip][offset:offset + length])

    def _word(self, bank, address):
        pair = self.read(bank, address, 2)
        return (pair[0] << 8) | pair[1]

    # -- the directory ----------------------------------------------

    def _record(self, index, kind, bank, address):
        """Reads one 5-byte {bank, start, end} record, or None if it is
        not a plausible one -- which is how a table's end is found."""
        raw = self.read(bank, address, 5)
        if len(raw) < 5:
            return None

        sample_bank, start, end = raw[0], (raw[1] << 8) | raw[2], (raw[3] << 8) | raw[4]
        window_end = WINDOW_BASE + WINDOW_SIZE
        if (raw[0] & 0xE0) not in CHIP_SELECT:
            return None
        if not WINDOW_BASE <= start <= window_end or not start <= end <= window_end:
            return None

        try:
            chip, offset = self.resolve(sample_bank, start)
        except ValueError:
            return None

        return Block(index=index, kind=kind, bank=sample_bank, start=start,
                     end=end, chip=chip, offset=offset, size=end - start)

    def dac_blocks(self):
        """The DAC table: 5-byte records one after another."""
        base = self._word(self.directory_bank, self.dac_table)
        blocks, index = [], 0
        while True:
            block = self._record(index, "dac", self.directory_bank, base + index * 5)
            if block is None:
                return blocks
            blocks.append(block)
            index += 1

    def cvsd_blocks(self):
        """The CVSD table: 16-bit pointers, each to a 5-byte record."""
        base = self._word(self.directory_bank, self.cvsd_table)
        blocks, index = [], 0
        while True:
            pointer = self._word(self.directory_bank, base + index * 2)
            if not WINDOW_BASE <= pointer < WINDOW_BASE + WINDOW_SIZE:
                return blocks
            block = self._record(index, "cvsd", self.directory_bank, pointer)
            if block is None:
                return blocks
            blocks.append(block)
            index += 1

    def blocks(self):
        return self.dac_blocks() + self.cvsd_blocks()

    def runs(self, blocks=None):
        """Groups blocks that sit back to back in the ROM.

        **These are ROM page fills, not phrases.** Every run begins at
        window address `$4002` -- two bytes past the start of a 32 KB
        page -- and ends within 30 to 90 bytes of the page top, so what
        a run collects is however many unrelated samples the packer fit
        into one page. Their indices are not even consecutive. Playing
        a run back gives you the page as the chip reads it, which is
        useful for hearing joins and boundaries; it is not a recording.

        Returns runs in ROM order, each a list of blocks. A block with
        no neighbours is a run of one, and empty table slots are left
        out -- they have no bytes to join to anything.
        """
        blocks = [b for b in (self.blocks() if blocks is None else blocks) if b.size]
        by_start = {}
        for block in blocks:
            by_start.setdefault((block.chip, block.offset), block)

        following, preceded = {}, set()
        for block in blocks:
            nxt = by_start.get((block.chip, block.offset + block.size))
            if nxt is not None and nxt.index != block.index:
                following[block.index] = nxt
                preceded.add(nxt.index)

        runs = []
        for block in blocks:
            if block.index in preceded:
                continue
            chain, seen = [block], {block.index}
            while chain[-1].index in following:
                nxt = following[chain[-1].index]
                if nxt.index in seen:
                    break
                chain.append(nxt)
                seen.add(nxt.index)
            runs.append(chain)
        return runs

    # -- contents ---------------------------------------------------

    # -- the power-on checksum ---------------------------------------

    def checksum_banks(self):
        """The banks the self test checks, in its own order.

        Empty when the list does not read as one -- a synthetic image,
        or a game whose list lives elsewhere -- rather than guessing.
        """
        u18 = self.chips["u18"]
        at = len(u18) - 0x4000 + self.checksum_list - 0xC000
        banks = []
        while 0 <= at < len(u18) and u18[at] != 0xFF and len(banks) < 64:
            bank = u18[at]
            try:
                self.resolve(bank, WINDOW_BASE)
            except ValueError:
                return []
            banks.append(bank)
            at += 1
        return banks

    def _page_sum(self, bank):
        return sum(self.read(bank, WINDOW_BASE, WINDOW_SIZE)) & 0xFF

    def bad_checksums(self):
        """The banks whose page would fail the self test as they stand."""
        return [bank for bank in self.checksum_banks()
                if (-self._page_sum(bank)) & 0xFF != bank]

    def correction_byte(self, bank):
        """Where a page's checksum correction goes, as (chip, offset).

        `$BFFF` on every page but one: U18's top page holds the fixed
        bank, and its `$BFFF` is the low byte of the reset vector.
        There the correction goes just below the fixed bank, at `$7FFF`,
        in the $FF padding after the page's last block.
        """
        chip, offset = self.resolve(bank, CHECKSUM_BYTE)
        if chip == "u18" and offset >= len(self.chips["u18"]) - 0x4000:
            chip, offset = self.resolve(bank, FIXED_PAGE_CORRECTION)
        return chip, offset

    def fix_checksums(self):
        """Rewrites `$BFFF` on every page that would fail; returns those.

        Pages that already pass are left byte for byte as they were, so
        an untouched page in a patched set is still the original page.
        """
        fixed = []
        for bank in self.bad_checksums():
            chip, offset = self.correction_byte(bank)
            image = self.chips[chip]
            rest = (self._page_sum(bank) - image[offset]) & 0xFF
            image[offset] = (-bank - rest) & 0xFF
            fixed.append(bank)
        return fixed

    def extract(self, block):
        chip = self.chips[block.chip]
        return bytes(chip[block.offset:block.offset + block.size])

    def write(self, block, data):
        """Replaces a block's bytes in place, touching nothing else."""
        if len(data) > block.size:
            raise ValueError(
                "el bloque %d (%s) admite %d bytes y se le dan %d; usa fit_payload"
                % (block.index, block.kind, block.size, len(data))
            )
        chip = self.chips[block.chip]
        chip[block.offset:block.offset + len(data)] = data

    def save(self, paths):
        """Writes the images back out, given {chip name: path}."""
        for chip, path in paths.items():
            with open(path, "wb") as handle:
                handle.write(bytes(self.chips[chip]))


def duration(kind, size, rate):
    """Seconds a block of `size` bytes lasts.

    A DAC byte is one sample; a CVSD byte is eight bits, each one
    sample at the bit clock. Neither rate is calibrated yet -- the
    sound board derives both from a YM2151 timer -- so the caller
    supplies it and a wrong value changes duration, not intelligibility.
    """
    if rate is None:
        return None
    return (size * 8 if kind == "cvsd" else size) / float(rate)


def fit_payload(block, payload, rate=None):
    """Makes `payload` exactly fill `block`, and says what that cost.

    A block cannot grow: its end address is baked into the table and
    the next block starts there. So anything longer is truncated and
    anything shorter is padded with that stream's silence, and the
    report carries both figures plus the duration the block can hold.
    """
    silence = CVSD_SILENCE if block.kind == "cvsd" else DAC_SILENCE

    truncated = max(0, len(payload) - block.size)
    padded = max(0, block.size - len(payload))
    data = bytes(payload[:block.size]) + bytes([silence]) * padded

    return data, FitReport(
        size=block.size,
        payload=len(payload),
        truncated=truncated,
        padded=padded,
        capacity_seconds=duration(block.kind, block.size, rate),
        payload_seconds=duration(block.kind, min(len(payload), block.size), rate),
    )
