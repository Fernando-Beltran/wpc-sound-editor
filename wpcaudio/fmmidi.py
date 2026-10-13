"""The music as MIDI, and MIDI as the music.

Export runs the board, the same way a render does, and turns what it
writes to the YM2151 into notes: a key-on (register $08 with any slot
bit set) starts a note on its channel at the key code that channel holds
in $28+ch, and the next key-off or key-on of that channel ends it. That
needs no understanding of the script VM at all -- it reads the chip,
not the program -- so it works for every music command and is exact by
construction. DAC samples the music starts (the drum kit, on command
$0E) go to MIDI channel 10, one pitch per block.

The board's own pitch is the YM2151 key code: octave in bits 6-4, note
in bits 3-0, counting from C# and skipping codes 3, 7, 11 and 15. With
the chip's 3.579545 MHz clock, code $4A (octave 4, A) is 440 Hz.
"""

from wpcaudio.board import CPU_CLOCK, SoundBoard
from wpcaudio.midi import Note

SETTLE = 0.05
DRUMS = 9
DRUM_BASE = 35                  # acoustic bass drum, for DAC block 0
VELOCITY = 100


def kc_to_midi(kc):
    """YM2151 key code to MIDI note number."""
    octave, code = (kc >> 4) & 0x07, kc & 0x0F
    return 61 + 12 * (octave - 4) + code - code // 4


def midi_to_kc(pitch):
    """MIDI note number to the nearest YM2151 key code, or None.

    The chip spans eight octaves from C#0; anything outside is refused
    rather than folded, so the caller can say which notes it dropped.
    """
    step = pitch - 61 + 48          # semitones above C#0
    if not 0 <= step < 96:
        return None
    octave, index = divmod(step, 12)
    return (octave << 4) | (index + index // 3)


def notes_from_log(writes, samples=(), dac_index=None, start_cycle=0):
    """Turns a register log (and sample starts) into Notes, in seconds."""
    def seconds(cycle):
        return (cycle - start_cycle) / float(CPU_CLOCK)

    kc = [0] * 8
    sounding = {}
    notes = []
    for write in writes:
        if 0x28 <= write.register < 0x30:
            kc[write.register - 0x28] = write.value
        elif write.register == 0x08:
            channel = write.value & 0x07
            if channel in sounding:
                begun, pitch = sounding.pop(channel)
                notes.append(Note(begun, seconds(write.cycle) - begun, pitch,
                                  VELOCITY, channel))
            if write.value & 0x78:
                sounding[channel] = (seconds(write.cycle), kc_to_midi(kc[channel]))
    end = max([seconds(w.cycle) for w in writes] or [0])
    for channel, (begun, pitch) in sounding.items():
        notes.append(Note(begun, max(0.01, end - begun), pitch, VELOCITY, channel))

    for start in samples:
        if start.stream != "dac" or dac_index is None:
            continue
        index = dac_index.get((start.bank, start.start, start.end + 1),
                              dac_index.get((start.bank, start.start, start.end)))
        if index is None:
            continue
        notes.append(Note(seconds(start.cycle), 0.1, DRUM_BASE + index % 46,
                          VELOCITY, DRUMS))
    return sorted(notes, key=lambda n: (n.start, n.channel, n.pitch))


def export(rom, command, seconds=10.0):
    """Runs a command and returns the notes it played."""
    board = SoundBoard(rom)
    board.boot()
    board.run(SETTLE)
    first_write, first_sample = len(board.ym.writes), len(board.samples)
    start = board.cpu.cycles
    board.send(command)
    board.run(seconds)
    dac_index = {(b.bank, b.start, b.end): b.index for b in rom.dac_blocks()}
    return notes_from_log(board.ym.writes[first_write:], board.samples[first_sample:],
                          dac_index, start)
