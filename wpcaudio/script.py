"""Reaching the voice programs a command runs, and repointing them.

A command does not name a sound. It names a `{kind, parameter}` pair,
and for the two music kinds the parameter leads to a header and from
there to one 16-bit program per voice. Those programs are what the
interpreter runs, and `$F98C`-style speech aside, what they contain is
FM: note, duration, level, over and over.

Two of the interpreter's sixty-three opcodes play recorded audio
instead:

    $00 idx .. $FF   play these CVSD blocks, in order
    $08 idx dur16    play this DAC block

So a music cue can be made to play a recording by overwriting the start
of its program with `$00`, the block indices, and `$FF` -- three bytes
for one block. Verified on the real ROM: patching command `$0E`'s first
program to `00 2A FF` stops the FM and starts `cvsd:42`.

Nothing here decides *which* blocks: there is no free space in these
images, so whatever a replacement uses, it takes from something else.
That is the caller's choice to make and to show.
"""

from collections import namedtuple

DIRECTORY_PAGE = 0x060000
COMMAND_TABLE = 0x400F
KIND4_TABLE = 0x4007
KIND1_TABLE = 0x4011
KIND1_VOICES = 10

PLAY_CVSD = 0x00
PLAY_DAC = 0x08
END_OF_LIST = 0xFF
# `$01`-`$07` all land on the same handler, which returns without
# touching the script pointer: the voice simply stops.
STOP_VOICE = 0x01

Program = namedtuple("Program", "address offset")


class NoPrograms(Exception):
    """This command does not run voice programs."""


def _fixed_base(image):
    return len(image) - 0x4000


def rom_offset(image, address):
    """Where a script address lives in the U18 image.

    Scripts are addressed either in the window, with the directory bank
    selected, or in the fixed bank at the top of the chip.
    """
    if address >= 0xC000:
        return _fixed_base(image) + address - 0xC000
    return DIRECTORY_PAGE + address - 0x4000


def _byte(image, address):
    return image[rom_offset(image, address)]


def _word(image, address):
    return (_byte(image, address) << 8) | _byte(image, address + 1)


def command_entry(image, command):
    """The `{kind, parameter}` a command byte resolves to."""
    table = _word(image, COMMAND_TABLE)
    offset = rom_offset(image, table) + command * 2
    return image[offset], image[offset + 1]


def programs_of(image, command):
    """The voice programs a command starts, in order, without repeats."""
    kind, parameter = command_entry(image, command)

    if kind == 4:
        head = _word(image, _word(image, KIND4_TABLE) + parameter * 2)
        mask = _byte(image, head)
        pointers = [_word(image, head + 1 + 2 * i)
                    for i in range(bin(mask).count("1"))]
    elif kind == 1:
        entry = _word(image, _word(image, KIND1_TABLE) + parameter * 2)
        pointers = [_word(image, entry + i * 2) for i in range(KIND1_VOICES)]
    else:
        raise NoPrograms("el comando no es de tipo 1 ni 4 (es %d)" % kind)

    seen, found = set(), []
    for pointer in pointers:
        if 0x4000 <= pointer <= 0xFFFF and pointer not in seen:
            seen.add(pointer)
            found.append(Program(pointer, rom_offset(image, pointer)))
    if not found:
        raise NoPrograms("el comando $%02X no apunta a ningun programa" % command)
    return found


# The script clock is the 16-bit counter at `$36`, which the FIRQ bumps
# once every `$38` interrupts -- 11 on this program -- and each FIRQ
# emits two DAC samples. So one tick is 22 DAC bytes, exactly: measured
# on the emulated board for three blocks, start of stream to its end.
# Measured in FIRQs rather than seconds, which is what makes it exact:
# whatever the interrupt rate, samples and ticks advance together.
DAC_BYTES_PER_TICK = 22
# Bit 7 is the mode flag the shared tail propagates; the game's own
# programs write `$08` as `$88`, and a bare `$08` at the head of a
# program never starts a stream.
PLAY_DAC_FLAGGED = 0x80 | PLAY_DAC
SILENCE_VOICE_FLAGGED = 0x80 | 0x15


def dac_ticks(size):
    """Ticks a DAC block of `size` bytes takes to play out.

    Rounded up: a duration one tick short would let the next `$88` cut
    the last few samples, while one tick long only leaves the DAC
    holding its final value for under two milliseconds.
    """
    return -(-size // DAC_BYTES_PER_TICK)


def play_dac_bytes(blocks):
    """The script that plays these DAC blocks back to back, then stops.

    `blocks` is `(index, size)` pairs. Each becomes `$88 idx dur16`, the
    duration being how long the voice waits before its next opcode --
    which, set to the block's own length, starts the next block exactly
    as this one runs out.
    """
    if not blocks:
        raise ValueError("hace falta al menos un bloque")
    out = bytearray()
    for index, size in blocks:
        if not 0 <= index <= 0xFF:
            raise ValueError("indice fuera de rango: %r" % index)
        ticks = dac_ticks(size)
        if not 0 < ticks <= 0xFFFF:
            raise ValueError("el bloque %d no tiene una duracion valida" % index)
        out += bytes([PLAY_DAC_FLAGGED, index, ticks >> 8, ticks & 0xFF])
    return bytes(out) + bytes([SILENCE_VOICE_FLAGGED])


def play_cvsd_bytes(indices):
    """The script that plays these CVSD blocks and then stops."""
    if not indices:
        raise ValueError("hace falta al menos un bloque")
    for index in indices:
        if not 0 <= index <= 0xFF:
            raise ValueError("indice fuera de rango: %r" % index)
        if index == END_OF_LIST:
            raise ValueError("$FF termina la lista, no puede ser un indice")
    return bytes([PLAY_CVSD]) + bytes(indices) + bytes([END_OF_LIST])


# `$15` stops a voice and resets its YM2151 registers. It is the stop to
# use at the head of a program: `$01` there, tried on the emulated
# board, silences the CVSD voice as well as the FM, while `$15` leaves
# the recording playing and the FM at zero key-ons.
SILENCE_VOICE = 0x15


def program_users(image):
    """Every voice program, and the commands that start it.

    Programs are shared: a patch to one changes every command that runs
    it, and that is something the user has to be told.
    """
    users = {}
    for command in range(256):
        try:
            programs = programs_of(image, command)
        except NoPrograms:
            continue
        for program in programs:
            users.setdefault(program.address, set()).add(command)
    return users


def room(address, starts):
    """How many bytes a program can be overwritten with.

    Programs are packed, often two bytes apart, so the next known
    program start is a hard wall: writing past it would rewrite a voice
    some other command runs.
    """
    later = [start for start in starts if start > address]
    wall = min(later) if later else 0x10000
    # In the window the page ends at $BFFF, which is not script: it is
    # the page's power-on checksum correction (wpcaudio.soundrom).
    if address < 0xBFFF <= wall:
        wall = 0xBFFF
    return wall - address


Patch = namedtuple("Patch", "command host writes shared")


def recording_patch(image, command, indices, users=None, kind="cvsd", sizes=None):
    """What to write so that `command` plays these blocks instead.

    `kind` says which chip: CVSD blocks go in one `$00` list, DAC blocks
    one `$88` each, which needs every block's `sizes` for its duration.

    One program -- the one with the most room -- becomes the play list,
    `$00 idx.. $FF` and then `$15` so it stops cleanly afterwards; every
    other program of the command becomes a lone `$15`, which silences
    its FM voice. Returns the writes as `(offset, bytes)` pairs, which
    is all a revert needs to remember, and the other commands that
    share any of those programs.
    """
    users = program_users(image) if users is None else users
    programs = programs_of(image, command)
    if kind == "dac":
        listing = play_dac_bytes(list(zip(indices, sizes)))
    else:
        listing = play_cvsd_bytes(indices) + bytes([SILENCE_VOICE])
    starts = list(users)

    host = max(programs, key=lambda p: room(p.address, starts))
    space = room(host.address, starts)
    if space < len(listing):
        fits = (space - 1) // 4 if kind == "dac" else space - 3
        raise ValueError(
            "no caben %d bloques: el programa $%04X deja sitio para %d"
            % (len(indices), host.address, max(0, fits)))

    writes = [(host.offset, listing)]
    writes += [(p.offset, bytes([SILENCE_VOICE]))
               for p in programs if p is not host]
    shared = sorted({other for p in programs
                     for other in users.get(p.address, ()) if other != command})
    return Patch(command, host, writes, shared)


def apply(image, writes):
    """Writes the patch, returning what was there so it can go back."""
    before = []
    for offset, data in writes:
        before.append((offset, bytes(image[offset:offset + len(data)])))
        image[offset:offset + len(data)] = data
    return before
