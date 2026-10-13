"""Hearing a command's music, by running the board that makes it.

On a pre-DCS board the music is not stored as audio at all: it is a
stream of register writes the 6809 program feeds to the YM2151. There
is no block in the ROM to extract and no file to play. The only way to
hear it is to run the program, send it the command byte the game's CPU
would send, and record what it writes to the chip.

So that is what this does: emulate the board, log the writes, save them
as a VGM, and hand that to VGMPlay for the actual synthesis. The result
is cached, because a render costs a few seconds of real time and the
answer never changes for the same ROM and command.

The timer acknowledgement is dropped from the log. The interrupt
handler writes register `$14` several thousand times a second and it
carries no music; leaving it in would make a player stutter over tens
of thousands of no-ops.
"""

import pathlib

from wpcaudio.board import CPU_CLOCK, YM_CLOCK, SoundBoard
from wpcaudio.vgm import write_vgm
from wpcaudio.vgmplay import render

TIMER_ACK = 0x14
DEFAULT_SECONDS = 10.0
SETTLE = 0.05


def record(rom, command, seconds=DEFAULT_SECONDS, on_progress=None):
    """Runs one command on the emulated board; returns its register log."""
    board = SoundBoard(rom)
    board.boot()
    board.run(SETTLE)

    first = len(board.ym.writes)
    board.send(command)
    board.run(seconds, on_progress=on_progress)

    log = [w for w in board.ym.writes[first:] if w.register != TIMER_ACK]
    if not log:
        return [], board
    start = log[0].cycle
    return [w._replace(cycle=w.cycle - start) for w in log], board


# Bumped whenever the emulation changes what a render sounds like, so a
# cache from an older emulator is not served as current. 2: the YM2151's
# delayed IRQ clear, which stopped the board losing FIRQs and put the
# music back at its real tempo (about 10% faster on command $0E).
RENDER_VERSION = 2


def wav_path(cache, game_key, command, seconds):
    cache = pathlib.Path(cache)
    return cache / ("%s_cmd%02X_%ds_v%d.wav"
                    % (game_key, command, int(seconds), RENDER_VERSION))


def music_wav(rom, game_key, command, cache, seconds=DEFAULT_SECONDS,
              on_progress=None):
    """The command's music as a WAV, rendering it the first time only."""
    target = wav_path(cache, game_key, command, seconds)
    if target.exists():
        return target

    def running(done, total):
        if on_progress:
            on_progress("emulating", done, total)

    log, _ = record(rom, command, seconds, on_progress=running)
    if not log:
        return None

    if on_progress:
        on_progress("synthesising", seconds, seconds)
    target.parent.mkdir(parents=True, exist_ok=True)
    vgm = target.with_suffix(".vgm")
    write_vgm(vgm, log, cpu_clock=CPU_CLOCK, ym_clock=YM_CLOCK)
    rendered = render(vgm)
    if rendered != target:
        rendered.replace(target)
    return target


def render_rom(rom, command, seconds, target, on_progress=None):
    """Renders a command from any set of images, cache or no cache.

    For images that are being edited -- a compiled song, an instrument
    audition -- where the cache keyed by game and command would answer
    with the original music.
    """
    def running(done, total):
        if on_progress:
            on_progress("emulating", done, total)

    log, _ = record(rom, command, seconds, on_progress=running)
    if not log:
        return None
    if on_progress:
        on_progress("synthesising", seconds, seconds)
    target = pathlib.Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    vgm = target.with_suffix(".vgm")
    write_vgm(vgm, log, cpu_clock=CPU_CLOCK, ym_clock=YM_CLOCK)
    rendered = render(vgm)
    if rendered != target:
        rendered.replace(target)
    return target
