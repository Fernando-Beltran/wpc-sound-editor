"""MIDI into the sound board's own music scripts.

The script VM plays every note through two opcodes, and a song needs
little else. What this emits, per voice, after that voice's original
instrument set-up:

    $3B g            release gap: key-off `g` ticks before a note ends
    $3A kc dd        note: key code, duration (1 byte; kc carries bit 7)
    $3A kc dddd      note, 2-byte duration (kc without bit 7)
    $35 dd           key-off and rest, 1-byte duration
    $0B dddd         key-off and rest, 2-byte duration
    $32 addr         GOTO -- back to the top, to loop
    $15              stop the voice

Every opcode is written with bit 7 set, as the game's own programs do:
the shared tail propagates that bit as a mode flag, and a script that
drops it reads its operands differently.

Durations are ticks of the script clock, which the FIRQ advances every
`$38` = 11 interrupts: 5593 Hz / 11 = 508.5 ticks a second. They are
absolute -- each voice keeps its own running total -- so rounding never
accumulates into drift between voices.

Where the notes go. A song is longer than the program it replaces, and
the images have no free space, so the note streams are written into a
borrowed sample block (the caller picks it and is told what it costs).
Each voice keeps its first bytes -- the CALLs that load its instrument,
which only work from the directory bank -- and right after them jumps
there with `$3E bank addr`.
"""

import bisect
from collections import namedtuple

from wpcaudio.fmmidi import DRUMS, midi_to_kc

FIRQ_RATE = 3579545.0 / 640.0
TICK_RATE = FIRQ_RATE / 11.0

NOTE, REST1, REST2, GATE = 0xBA, 0xB5, 0x8B, 0xBB
GOTO, FAR_JUMP, STOP = 0xB2, 0xBE, 0x95
RELEASE_GAP = 1

Voice = namedtuple("Voice", "address channel prologue")
Placed = namedtuple("Placed", "voice notes")
Compiled = namedtuple("Compiled", "writes streams dropped out_of_range unused shared")


class SongTooBig(ValueError):
    """The note streams need more room than the block has."""

    def __init__(self, needed, size):
        super().__init__("la cancion necesita %d bytes y el bloque tiene %d" % (needed, size))
        self.needed, self.size = needed, size


def measure(notes, voices, loop=True, priority=None):
    """How many bytes the song's streams take, before choosing a block."""
    groups = parts(voices)
    shares, _ = allocate(notes, groups, priority)
    total = max([ticks(n.start + n.length) for n in notes if n.channel != DRUMS] or [0])
    return sum(len(stream(shares[i], 0, total=total, loop=loop)[0])
               for i in range(len(groups)) if shares[i])


def _cut(notes, at):
    """The song up to `at` seconds: later notes go, held ones are shortened."""
    return [n._replace(length=min(n.length, at - n.start)) for n in notes if n.start < at]


def fit_song(notes, voices, size, loop=True, priority=None, mode="cut"):
    """Makes a song fit `size` bytes, saying what it took.

    `cut` keeps the longest opening that fits, found by bisection over
    the note starts -- the size grows with every note kept, so the
    search is sound. `thin` first drops whole channels from the bottom
    of the priority list, keeping at least one, and cuts only if that
    still is not enough. A song that already fits comes back untouched:
    a block with room to spare needs no filling, because the song's
    closing GOTO already repeats it for as long as the command plays.

    Returns (notes, report) with `cut_at` (seconds, or None), `removed`
    (MIDI channels, 0-based) and `needed` (bytes before any fitting).
    """
    needed = measure(notes, voices, loop, priority)
    report = {"needed": needed, "cut_at": None, "removed": [], "spare": size - needed}
    if needed <= size:
        return notes, report

    if mode == "thin":
        order = list(priority or sorted({n.channel for n in notes if n.channel != DRUMS}))
        order += sorted({n.channel for n in notes if n.channel != DRUMS} - set(order))
        while len(order) > 1:
            gone = order.pop()
            notes = [n for n in notes if n.channel != gone]
            report["removed"].append(gone)
            if measure(notes, voices, loop, order) <= size:
                report["spare"] = size - measure(notes, voices, loop, order)
                return notes, report
        priority = order

    starts = sorted({n.start for n in notes})
    low, high = 0, len(starts)          # starts[:low] fit; starts[:high] may not
    while low < high:
        middle = (low + high + 1) // 2
        at = starts[middle] if middle < len(starts) else float("inf")
        if measure(_cut(notes, at), voices, loop, priority) <= size:
            low = middle
        else:
            high = middle - 1
    if low == 0:
        raise SongTooBig(measure(_cut(notes, starts[1] if len(starts) > 1 else 1e9),
                                 voices, loop, priority), size)
    at = starts[low] if low < len(starts) else max(n.start + n.length for n in notes)
    fitted = _cut(notes, at)
    report["cut_at"] = at
    report["spare"] = size - measure(fitted, voices, loop, priority)
    return fitted, report


def ticks(seconds):
    return int(round(seconds * TICK_RATE))


def allocate(notes, voices, priority=None):
    """Shares notes out among voices, one sounding note per voice.

    Channels are placed whole, in `priority` order (default: ascending
    MIDI channel, which is lead then bass in most files), each note
    going to a voice that is silent for its whole length -- preferring
    one already playing that channel, so a part keeps its timbre. A
    later channel only gets the gaps the earlier ones left, so when
    there are more notes than voices it is the accompaniment that
    thins out, not the tune. Drum-channel notes are left out: the FM has
    no drum kit. Returns ({voice index: [notes]}, dropped).
    """
    order = list(priority or sorted({n.channel for n in notes}))
    order += sorted({n.channel for n in notes} - set(order))
    taken = [[] for _ in voices]            # (start, end) spans per voice
    owner = [None] * len(voices)
    shares = {i: [] for i in range(len(voices))}
    dropped = []

    def free(i, start, end):
        # `taken[i]` is kept sorted and never overlaps, so only the two
        # neighbours of where this span would go can collide with it.
        spans = taken[i]
        at = bisect.bisect_left(spans, (start, end))
        if at < len(spans) and spans[at][0] < end:
            return False
        return not (at > 0 and spans[at - 1][1] > start)

    for channel in order:
        if channel == DRUMS:
            continue
        mine = sorted((n for n in notes if n.channel == channel),
                      key=lambda n: (ticks(n.start), -n.pitch))
        for note in mine:
            start = ticks(note.start)
            end = max(start + 1, ticks(note.start + note.length))
            candidates = [i for i in range(len(voices)) if free(i, start, end)]
            if not candidates:
                dropped.append(note)
                continue
            same = [i for i in candidates if owner[i] == channel]
            unused = [i for i in candidates if owner[i] is None]
            choice = (same or unused or candidates)[0]
            bisect.insort(taken[choice], (start, end))
            shares[choice].append(note)
            if owner[choice] is None:
                owner[choice] = channel
    for i in shares:
        shares[i].sort(key=lambda n: n.start)
    return shares, dropped


def _duration(value):
    return bytes([value >> 8, value & 0xFF])


def stream(notes, base, total=None, loop=True, gap=RELEASE_GAP):
    """One voice's script, to be placed at window address `base`.

    Returns (bytes, out_of_range): notes the chip cannot pitch are left
    out rather than folded into another octave.
    """
    out = bytearray([GATE, gap])
    clock, skipped = 0, []
    ordered = sorted(notes, key=lambda n: n.start)
    for i, note in enumerate(ordered):
        kc = midi_to_kc(note.pitch)
        if kc is None:
            skipped.append(note)
            continue
        start = max(clock, ticks(note.start))
        end = ticks(note.start + note.length)
        if i + 1 < len(ordered):
            end = min(end, ticks(ordered[i + 1].start) - gap)
        sounding = max(1, end - start)
        if start > clock:
            out += _rest(start - clock)
        length = sounding + gap
        if length <= 0xFF:
            out += bytes([NOTE, kc | 0x80, length])
        else:
            out += bytes([NOTE, kc]) + _duration(min(length, 0xFFFF))
        clock = start + length
    if total is not None and total > clock:
        out += _rest(total - clock)
    out += bytes([GOTO]) + _duration(base + 2) if loop else bytes([STOP])
    return bytes(out), skipped


def _rest(length):
    out = bytearray()
    while length > 0:
        piece = min(length, 0xFFFF)
        out += bytes([REST1, piece]) if piece <= 0xFF else bytes([REST2]) + _duration(piece)
        length -= piece
    return bytes(out)


def parts(voices):
    """Voices that share their set-up's end play one part, in unison.

    The game doubles parts this way: `$7FB7` is three bytes of detune
    followed by the program at `$7FBA`, so both voices reach the same
    music at `$7FC0` and play it together -- a chorus. One jump there
    keeps that, and the pair counts as one voice for allocation.
    """
    grouped = {}
    for voice in voices:
        grouped.setdefault(voice.prologue, []).append(voice)
    # The part's address is where its voices' code becomes one: the
    # latest start in the group (`$7FBA` for the pair above). An
    # instrument written there replaces the set-up for both voices and
    # leaves the detune in front of it alone.
    return [Voice(max(v.address for v in group), group[0].channel, prologue)
            for prologue, group in grouped.items()]


def compile_song(notes, voices, block, loop=True, silence=(), room=None, priority=None,
                 instruments=None):
    """Plans every write that makes `voices` play `notes`.

    `voices` are the command's FM programs (Voice: address, YM channel,
    where its set-up ends); `block` is the borrowed sample block the
    streams go into; `silence` are programs to stop outright (a control
    voice that starts other sounds, the drums if unwanted). Parts left
    without notes are stopped where their music would begin. Returns a
    Compiled with `(window address, bytes)` writes in the directory bank,
    and the streams with their addresses inside the block.
    """
    groups = parts(voices)
    shares, dropped = allocate(notes, groups, priority)
    total = max([ticks(n.start + n.length) for n in notes if n.channel != DRUMS] or [0])

    # Sizes do not depend on where a stream lands (the GOTO's operand is
    # always two bytes), so the whole song is measured before anything
    # is placed, and a block too small says how much it would take.
    needed = sum(len(stream(shares[i], 0, total=total, loop=loop)[0])
                 for i in range(len(groups)) if shares[i])
    if needed > block.end - block.start:
        raise SongTooBig(needed, block.end - block.start)

    streams, out_of_range, at = [], [], block.start
    writes = [(address, bytes([STOP])) for address in silence]
    unused = []
    for i, voice in enumerate(groups):
        if not shares[i]:
            writes.append((voice.prologue, bytes([STOP])))
            unused.append(voice.address)
            continue
        data, skipped = stream(shares[i], at, total=total, loop=loop)
        out_of_range += skipped
        streams.append((at, data))
        jump = bytes([FAR_JUMP, block.bank]) + _duration(at)
        chosen = (instruments or {}).get(i)
        if chosen is not None:
            # A different instrument replaces the set-up outright, so
            # the original one (and its volume offset) never runs.
            code, where = bytes(chosen) + jump, voice.address
        else:
            code, where = jump, voice.prologue
        if room is not None and room(where) < len(code):
            raise ValueError("no hay sitio en $%04X para %d bytes (parte %d)"
                             % (where, len(code), i + 1))
        writes.append((where, code))
        at += len(data)
    return Compiled(writes, streams, dropped, out_of_range, unused, [])
