"""Running the sound-script VM and watching it, voice by voice.

The main loop dispatches every opcode through one instruction,
`$DF71: JSR [A,X]`, with U pointing at the voice, `$04,U` holding the
opcode byte and `$06,U` the script pointer just past it. Hooking that
one address gives every opcode every voice executes, in order, with the
script clock (`$36`) at the time -- and the YM2151 writes that follow
can be pinned on the voice that made them.

This is ground truth. Reading the handlers statically has twice
produced confident wrong answers here; running them does not.
"""

import collections

from wpcaudio.board import SoundBoard

DISPATCH = 0xDF71
DP = 0x0200

Event = collections.namedtuple(
    "Event", "cycle tick voice channel bank address opcode raw writes")


def trace(rom, command, seconds):
    board = SoundBoard(rom)
    board.boot()
    board.run(0.05)
    cpu, mem, ym = board.cpu, board.memory, board.ym

    def rd(a):
        return mem.read(a & 0xFFFF)

    def word(a):
        return (rd(a) << 8) | rd(a + 1)

    events, current = [], None
    first = len(ym.writes)
    board.send(command)
    end = cpu.cycles + int(seconds * 2_000_000)
    while cpu.cycles < end:
        if cpu.pc == DISPATCH:
            u = cpu.u
            pointer = word(u + 6)
            bank = rd(u + 0x15)
            address = pointer - 1
            # Read the bytes from the ROM the voice is actually in.
            raw = rom.read(bank, address, 6) if address < 0xC000 else bytes(
                rom.chips["u18"][len(rom.chips["u18"]) - 0x4000 + address - 0xC000:][:6])
            current = Event(cpu.cycles, word(DP + 0x36), u, rd(u + 5) & 7, bank,
                            address, rd(u + 4), raw, [])
            events.append(current)
            mark = len(ym.writes)
        board._tick()
        if current is not None and len(ym.writes) > mark:
            current.writes.extend((w.register, w.value) for w in ym.writes[mark:])
            mark = len(ym.writes)
    return events




# Opcodes that begin the music proper. Everything a voice runs before
# the first of these is its set-up -- instrument CALLs, release gap,
# volume -- and stays where it is when a song is compiled in.
MUSIC_OPCODES = {0x00, 0x08, 0x0A, 0x0B, 0x0D, 0x0E, 0x15, 0x1A, 0x2F, 0x32, 0x35, 0x3A}
NOTE_OPCODES = {0x0A, 0x3A}

FmVoice = collections.namedtuple("FmVoice", "address channel prologue notes samples")


def voices(rom, command, seconds=1.5):
    """The voice programs a command starts, as the board runs them.

    For each voice: where its program starts, its YM channel, where its
    set-up ends (the first music opcode), and whether it played notes
    or samples -- which is what tells an FM voice from the drum kit.
    """
    found = collections.OrderedDict()
    for event in trace(rom, command, seconds):
        slot = found.get(event.voice)
        if slot is None:
            slot = found[event.voice] = {"address": event.address, "bank": event.bank,
                                         "channel": event.channel, "prologue": None,
                                         "notes": 0, "samples": 0}
        op = event.opcode & 0x3F
        if (slot["prologue"] is None and op in MUSIC_OPCODES
                and event.bank == slot["bank"]
                and slot["address"] <= event.address < slot["address"] + 0x200):
            slot["prologue"] = event.address
        if op in NOTE_OPCODES:
            slot["notes"] += 1
        if op == 0x08:
            slot["samples"] += 1
    return [FmVoice(s["address"], s["channel"], s["prologue"], s["notes"], s["samples"])
            for s in found.values() if s["prologue"] is not None]
