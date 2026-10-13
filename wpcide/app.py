"""The sound editor's logic, with no user interface attached.

Everything the front end can do lives here: take the ROM images the
user supplied, identify the machine, lay its sample blocks out as a map
of the chips, hand back any block as a playable WAV, replace one with
the user's own audio, and write the patched images out.

The split matters for where this is going. The front end talks to this
over a tiny HTTP API today, so the whole thing runs as a local
program; the same class is what a browser build would drive directly
through Pyodide, with the ROM never leaving the machine either way.
"""

import io
import json
import pathlib
import re
import zipfile

import numpy as np

from wpcide import paths

from wpcaudio.cvsd import Hc55516Decoder, unpack_bits
from wpcaudio.dac import decode_dac
from wpcaudio.filters import lowpass, to_int16
from wpcaudio.games import (candidate, crc32, flyer_url, identify, ipdb_url,
                            socket_for, verify)
from wpcaudio import dcs, fmcompile, fmtrace, midi, script
from wpcaudio.importer import prepare, resample
from wpcaudio.music import DEFAULT_SECONDS, music_wav, render_rom, wav_path
from wpcaudio.soundmap import SoundMap
from wpcaudio.thumbs import envelope
from wpcaudio.soundrom import Block, SoundRom, duration
from wpcaudio.wavio import read_wav, write_wav

# Measured off the board: the program sets YM2151 Timer A to a 640-clock
# period, so the FIRQ runs at 5593 Hz and each one emits two DAC samples
# and four CVSD bits.
FIRQ_RATE = 3579545.0 / 640.0
DAC_RATE = 2 * FIRQ_RATE
CVSD_BIT_RATE = 4 * FIRQ_RATE

SOCKETS = ("u18", "u15", "u14")

# Sound maps ship with the program; notes and rendered music are the
# user's and are written, so they go under their own data directory
# rather than wherever the program happened to be started from.
DEFAULT_MAPS = paths.bundled("mapas")

# What a block *is*, as the user hears it. The two tables say which chip
# plays a block, not what it holds -- the HC55516 carries effects as
# well as speech -- so this is the user's own classification, and it is
# what gives the map its colour.
# The keys are stored in the user's notes, so they are language
# neutral; the interface translates them for display. Renaming one
# would orphan every note that used it.
CATEGORIES = {
    "voice": "#4aa3df",
    "effect": "#e2a03f",
    "music": "#b07fd4",
    "ambience": "#4fbf9f",
    "jingle": "#e2704a",
    "unclassified": "#6b7280",
}

PAGE_SIZE = 0x8000
RECONSTRUCTION_CUTOFF = 3400.0


def _loudness(samples):
    """RMS of the louder passages: silence between phrases does not count."""
    data = np.asarray(samples, dtype=float)
    step = 1562
    frames = np.array([np.sqrt((data[i:i + step] ** 2).mean())
                       for i in range(0, max(1, len(data) - step), step)] or [0.0])
    loud = frames[frames > 0.2 * frames.max()] if frames.max() > 0 else frames
    return float(np.sqrt((loud ** 2).mean())) if len(loud) else 0.0


class NotLoaded(Exception):
    """Asked for something before a ROM set was identified."""


class Ide:
    """One editing session over one ROM set."""

    def __init__(self, notes_dir=None, maps_dir=None, cache_dir=None):
        self.images = {}
        self.game = None
        self.rom = None
        self.edits = {}
        self.staged = {}
        self.notes = {}
        # The user's own annotations are theirs, not the ROM's, so they
        # live beside it rather than in it.
        self.notes_dir = pathlib.Path(notes_dir or paths.user_data("notas"))
        # Which command plays which block, if anyone has scanned this
        # game. Built by observation on the emulated board, not by
        # reading the ROM -- see wpcaudio.soundmap.
        self.maps_dir = pathlib.Path(maps_dir or DEFAULT_MAPS)
        # Rendering a command's music means emulating the board for its
        # whole length, so the result is kept rather than remade.
        self.cache_dir = pathlib.Path(cache_dir or paths.user_data("cache"))
        self.sound_map = SoundMap()
        # Which CVSD blocks the speech handler can reach, by parameter.
        # Only used to say what a block would be taken away from.
        self.speech = {}
        # Music commands whose FM was replaced by a recording: command ->
        # the blocks it now plays and the program bytes it overwrote.
        self.music_edits = {}
        self._users = None
        self._voices = {}
        self._instruments = None
        self._midi_renders = {}
        # DCS: the set as supplied, its patched successor, and the tracks
        # re-programmed around the user's audio ({command: WAV bytes}).
        self.dcs = None
        self.dcs_patched = None
        self.dcs_edits = {}
        self.dcs_bitrate = None
        # Which board the user says they have, before any image says so.
        self.board = None
        self._shapes = {}
        self.progress = None

    # -- loading ----------------------------------------------------

    def add_image(self, socket, data):
        """Takes one ROM image. Sockets are 'u18', 'u15', 'u14'."""
        self.images[socket.lower()] = bytes(data)
        self.game = None
        self.rom = None

    def add_zip(self, data):
        """Takes a whole romset zip; each file finds its socket by CRC.

        Users mostly have their ROMs as PinMAME zips, and a DCS game is
        seven files: one drop beats seven. Files no known game claims
        are left out rather than guessed at.
        """
        taken = 0
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                image = archive.read(name)
                socket = socket_for(image)
                if socket:
                    self.add_image(socket, image)
                    taken += 1
        if not taken:
            raise ValueError("ningun fichero del zip es una ROM de sonido conocida")
        return taken

    @property
    def platform(self):
        return None if self.game is None else self.game.platform

    def load(self):
        """Identifies the set and builds the sample directory.

        Identification is by CRC32, which doubles as the import check:
        the offsets in the map were derived from these exact images, so
        a set that does not match is a set the map does not describe.
        """
        self.game = identify(self.images)
        if self.game is None:
            self.rom = None
            return None
        if self.game.platform == "dcs":
            return self._load_dcs()
        self.rom = SoundRom(
            u18=self.images["u18"],
            u15=self.images.get("u15"),
            u14=self.images.get("u14"),
            **self.game.directory
        )
        self.edits = {}
        self.staged = {}
        self.notes = self._read_notes()
        self.sound_map = SoundMap.load(self.maps_dir / ("%s.json" % self.game.key))
        self.speech = self._read_speech()
        self.music_edits = {}
        self._users = None
        self._voices = {}
        self._instruments = None
        self._midi_renders = {}
        self._shapes = {}
        return self.game

    def _read_speech(self):
        path = self.maps_dir / ("voz_%s.json" % self.game.key)
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))["entries"]
        except (OSError, ValueError, KeyError):
            return {}
        speech = {}
        for row in rows:
            for label in row.get("samples") or []:
                kind, _, index = label.partition(":")
                speech.setdefault((kind, int(index)), []).append(row["param"])
        return speech

    def _require(self):
        if self.rom is None:
            raise NotLoaded("todavia no hay un juego identificado")
        return self.rom

    def status(self):
        """What is loaded, socket by socket.

        The editor needs all three before it can say anything, so the
        interface shows them as the three sockets of the board and
        fills them in one at a time.
        """
        checks = self.report() or {}
        sockets = []
        # A pre-DCS board always has its three sockets, used or not; a DCS
        # board shows the sockets its game has (U2-U8 for STTNG).
        likely = self.game or candidate(self.images, self.board)
        shown = list(likely.roms) if likely.platform == "dcs" else SOCKETS
        for socket in shown:
            image = self.images.get(socket)
            check = checks.get(socket)
            sockets.append({
                "socket": socket,
                "loaded": image is not None,
                "size": len(image) if image else None,
                "crc": ("%08X" % crc32(image)) if image else None,
                "ok": None if check is None else check.ok,
            })
        return {
            "sockets": sockets,
            "platform": likely.platform,
            "game": None if self.game is None else
                    {"key": self.game.key, "name": self.game.name},
        }

    def report(self):
        """Per-socket CRC check against the identified game."""
        if self.game is None:
            return None
        return verify(self.images, self.game.key)

    # -- the map ----------------------------------------------------

    @staticmethod
    def rate(kind):
        return DAC_RATE if kind == "dac" else CVSD_BIT_RATE

    # -- the user's own notes ---------------------------------------

    def _notes_path(self):
        return self.notes_dir / ("%s.json" % self.game.key)

    def _read_notes(self):
        try:
            with open(self._notes_path(), encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    def _write_notes(self):
        try:
            self.notes_dir.mkdir(parents=True, exist_ok=True)
            with open(self._notes_path(), "w", encoding="utf-8") as handle:
                json.dump(self.notes, handle, ensure_ascii=False, indent=1)
        except OSError:
            pass          # a read-only install should still be usable

    def annotate(self, kind, index, note=None, colour=None, marked=None,
                 category=None):
        """Attaches the user's own label, category, colour and edit mark.

        `kind` is a block's (`dac`, `cvsd`) or the literal `music`, in
        which case `index` is a command byte: the music has no block to
        hang a note on, but it is just as worth labelling.
        """
        if kind == "music":
            if not 0 <= index <= 0xFF:
                raise KeyError("no existe el comando %d" % index)
        elif kind == "dcs":
            if index not in {t.command for t in self._require_dcs().catalog()[0]}:
                raise KeyError("no existe la pista %d" % index)
        else:
            self.find(kind, index)
        if category not in (None, "") and category not in CATEGORIES:
            raise ValueError("categoria desconocida: %r" % category)
        key = "%s:%d" % (kind, index)
        entry = dict(self.notes.get(key, {}))
        if note is not None:
            entry["note"] = note.strip()
        if colour is not None:
            entry["colour"] = colour.strip() or None
        if marked is not None:
            entry["marked"] = bool(marked)
        if category is not None:
            entry["category"] = category

        entry = {name: value for name, value in entry.items() if value}
        if entry:
            self.notes[key] = entry
        else:
            self.notes.pop(key, None)
        self._write_notes()
        return entry

    def export_notes(self):
        """The user's annotations as a document they own.

        Notes are the one thing here the user made rather than found,
        so they get to take them somewhere else -- another machine, a
        backup, the browser build when it exists.
        """
        return {
            "game": self.game.key if self.game else None,
            "notes": dict(self.notes),
        }

    def import_notes(self, payload, replace=False):
        """Takes annotations back in. Merges unless told to replace."""
        incoming = payload.get("notes") if isinstance(payload, dict) else None
        if not isinstance(incoming, dict):
            raise ValueError("el fichero no contiene anotaciones")
        if replace:
            self.notes = {}
        for key, entry in incoming.items():
            if isinstance(entry, dict):
                self.notes[key] = {name: value for name, value in entry.items()
                                   if name in ("note", "colour", "category", "marked")
                                   and value}
        self._write_notes()
        return len(incoming)

    def note_for(self, kind, index):
        return self.notes.get("%s:%d" % (kind, index), {})

    def block_info(self, block):
        note = self.note_for(block.kind, block.index)
        return {
            "kind": block.kind,
            "index": block.index,
            "bank": block.bank,
            "start": block.start,
            "end": block.end,
            "chip": block.chip,
            "offset": block.offset,
            "size": block.size,
            "seconds": duration(block.kind, block.size, self.rate(block.kind)),
            "edited": (block.kind, block.index) in self.edits,
            "note": note.get("note", ""),
            "category": note.get("category"),
            "colour": note.get("colour") or CATEGORIES.get(note.get("category")),
            "marked": bool(note.get("marked")),
            "commands": self.sound_map.commands_for(block.kind, block.index),
            "shape": self._shape(block),
        }

    def _shape(self, block):
        """The block's loudness silhouette, computed once and kept.

        Two hundred of these are drawn at a time, and the map is
        rebuilt after every edit, so recomputing them each time would
        make the editor feel slow for no reason.
        """
        key = (block.kind, block.index)
        cached = self._shapes.get(key)
        if cached is None:
            cached = envelope(block.kind, self.rom.extract(block))
            self._shapes[key] = cached
        return cached

    def chip_map(self):
        """The chips, their 32 KB pages, and every block in place.

        Laying blocks out at their real offsets is the whole point: the
        map is the chip, not a list. Everything a block does not cover
        is the 6809 program and its tables.
        """
        rom = self._require()
        blocks = rom.blocks()
        chips = []
        for socket in ("u18", "u15", "u14"):
            if socket not in rom.chips:
                continue
            size = len(rom.chips[socket])
            mine = [b for b in blocks if b.chip == socket and b.size]
            chips.append({
                "name": socket,
                "size": size,
                "pages": size // PAGE_SIZE,
                "page_size": PAGE_SIZE,
                "covered": sum(b.size for b in mine),
                "blocks": [self.block_info(b) for b in sorted(mine, key=lambda b: b.offset)],
            })
        return {
            "game": {"key": self.game.key, "name": self.game.name,
                     "ipdb": ipdb_url(self.game.key),
                     "flyers": flyer_url(self.game.key)},
            "rates": {"dac": DAC_RATE, "cvsd": CVSD_BIT_RATE, "firq": FIRQ_RATE},
            "chips": chips,
            "edits": sorted("%s:%d" % key for key in self.edits),
            "marked": sorted(key for key, note in self.notes.items()
                             if note.get("marked") and not key.startswith("music:")),
            "categories": CATEGORIES,
            "coverage": self.sound_map.coverage(
                [(b.kind, b.index) for b in blocks if b.size]),
        }

    def find(self, kind, index):
        for block in self._require().blocks():
            if block.kind == kind and block.index == index:
                return block
        raise KeyError("no existe el bloque %s:%d" % (kind, index))

    # -- listening and editing --------------------------------------

    def block_wav(self, kind, index, cutoff=RECONSTRUCTION_CUTOFF, original=False):
        """Decodes one block to WAV bytes, ready to play.

        `original` reads the image the user supplied rather than the
        edited one, so the two can be compared after a replacement
        without undoing it first.
        """
        rom = self._require()
        block = self.find(kind, index)
        if original:
            source = self.images[block.chip]
            data = bytes(source[block.offset:block.offset + block.size])
        else:
            data = rom.extract(block)
        rate = self.rate(kind)
        if kind == "dac":
            samples = decode_dac(data)
        else:
            samples = Hc55516Decoder().decode(unpack_bits(data))
        if cutoff and kind == "cvsd":
            samples = to_int16(lowpass(samples, rate, cutoff))

        buffer = io.BytesIO()
        write_wav(buffer, samples, int(rate))
        return buffer.getvalue()

    def _fit(self, block, offset, mode="trim"):
        samples, rate = self.staged[(block.kind, block.index)]
        return prepare(block, samples, rate, dac_rate=DAC_RATE,
                       bit_rate=CVSD_BIT_RATE, offset=offset, mode=mode)

    @staticmethod
    def _trim_report(block, report):
        return {
            "kind": block.kind,
            "index": block.index,
            "capacity_seconds": report.capacity_seconds,
            "source_seconds": report.source_seconds,
            "offset_seconds": report.offset_seconds,
            "offset": report.offset,
            "max_offset": report.max_offset,
            "truncated": report.truncated,
            "padded": report.padded,
            "rate": report.rate,
            "longer": report.source_samples > report.capacity_samples,
            "mode": report.mode,
            "speed": report.speed,
        }

    def stage(self, kind, index, wav_bytes):
        """Holds a WAV against a block without writing anything yet.

        The block cannot change size, so the only real decision left is
        *which* part of the audio goes in. Staging lets the front end
        ask that question -- play it, slide it -- before committing.
        """
        block = self.find(kind, index)
        samples, rate = read_wav(io.BytesIO(wav_bytes))
        self.staged[(kind, index)] = (samples, rate)
        return self._trim_report(block, self._fit(block, 0)[1])

    def fit_preview(self, kind, index, offset=0, mode="trim"):
        """What a given fit would cost, without writing anything."""
        block = self.find(kind, index)
        return self._trim_report(block, self._fit(block, offset, mode)[1])

    def preview(self, kind, index, offset=0, mode="trim"):
        """What the block would sound like, as a WAV.

        Encoded and decoded again, so what the user hears is what the
        ROM would hold -- the codec's own losses included, not the
        original file played back.
        """
        block = self.find(kind, index)
        data, _ = self._fit(block, offset, mode)
        rate = self.rate(kind)
        if kind == "dac":
            samples = decode_dac(data)
        else:
            samples = Hc55516Decoder().decode(unpack_bits(data))
            samples = to_int16(lowpass(samples, rate, RECONSTRUCTION_CUTOFF))
        buffer = io.BytesIO()
        write_wav(buffer, samples, int(rate))
        return buffer.getvalue()

    def commit(self, kind, index, offset=0, mode="trim"):
        """Writes the staged audio into the block."""
        rom = self._require()
        block = self.find(kind, index)
        data, report = self._fit(block, offset, mode)
        rom.write(block, data)
        self._shapes.pop((kind, index), None)
        self.edits[(kind, index)] = report
        result = self._trim_report(block, report)
        result["block"] = self.block_info(block)
        return result

    def replace(self, kind, index, wav_bytes, offset=0, mode="trim"):
        """Stage and commit in one go, for callers with no slider."""
        self.stage(kind, index, wav_bytes)
        return self.commit(kind, index, offset, mode)

    def revert(self, kind, index):
        """Puts a block back as the original image had it."""
        rom = self._require()
        block = self.find(kind, index)
        original = self.images[block.chip]
        rom.write(block, original[block.offset:block.offset + block.size])
        self._shapes.pop((kind, index), None)
        self.edits.pop((kind, index), None)
        self.staged.pop((kind, index), None)

    # -- the music ---------------------------------------------------

    def music_commands(self):
        """The commands that make music, loudest first.

        There is nothing in the ROM map to click for these: the music
        is register writes, not a block, so it gets its own list. The
        sounds a command also starts are shown beside it, because on
        the machine they are heard together.
        """
        commands = []
        for command, entry in self.sound_map._by_command.items():
            if not entry["keyon"]:
                continue
            commands.append({
                "command": command,
                "notes": entry["keyon"],
                "writes": entry["writes"],
                "samples": ["%s:%d" % pair for pair in entry["sounds"]],
                # Already rendered once, so this one plays at once.
                "note": self.note_for("music", command).get("note", ""),
                "category": self.note_for("music", command).get("category"),
                "colour": (self.note_for("music", command).get("colour")
                           or CATEGORIES.get(
                               self.note_for("music", command).get("category"))),
                "marked": bool(self.note_for("music", command).get("marked")),
                # Replaced by a recording: these blocks, in this order.
                "replaced": self._replaced_blocks(command),
                "ready": self._replaced_by(command) is not None
                         or self.game is not None and wav_path(
                    self.cache_dir, self.game.key, command,
                    DEFAULT_SECONDS).exists(),
            })
        return sorted(commands, key=lambda row: -row["notes"])

    def music_wav(self, command, seconds=DEFAULT_SECONDS):
        """Renders one command's music, or None if it makes none.

        A render is seconds of emulation, so it reports where it has
        got to in `self.progress` -- read over its own endpoint while
        this one is still blocking on the work.
        """
        self._require()
        owner = self._replaced_by(command)
        if owner is not None and self.music_edits[owner]["kind"] == "midi":
            return self._render_midi_edit(command, seconds)
        if owner is not None:
            return self.replaced_music_wav(owner)

        def note(stage, done, total):
            self.progress = {"command": command, "stage": stage,
                             "done": round(done, 2), "total": total}

        note("starting", 0, seconds)
        try:
            path = music_wav(self.rom, self.game.key, command,
                             self.cache_dir, seconds, on_progress=note)
        finally:
            self.progress = {"command": command, "stage": "done",
                             "done": seconds, "total": seconds}
        return None if path is None else path.read_bytes()

    # -- replacing the music with a recording -----------------------
    #
    # The FM cannot take a WAV: it is notes, not audio. What can is
    # either sample chip, and the script VM plays blocks for both from
    # inside any program. So a music command is made to play a recording
    # by rewriting its programs into a play list (wpcaudio.script) and
    # writing the user's audio into the blocks that list names. The
    # images have no free space, so those blocks are taken from whatever
    # they held -- which is why the caller picks them and sees the cost.
    #
    # The DAC sounds far better (8-bit PCM at 11 kHz against a 22 kbit/s
    # delta stream behind a 3.4 kHz filter); the CVSD has more room.

    MUSIC_KINDS = ("dac", "cvsd")

    def _program_users(self):
        if self._users is None:
            self._users = script.program_users(self.rom.chips["u18"])
        return self._users

    def _music_blocks(self, kind, indices):
        if kind not in self.MUSIC_KINDS:
            raise ValueError("tipo de bloque desconocido: %r" % kind)
        blocks = [self.find(kind, int(index)) for index in indices]
        if not blocks:
            raise ValueError("hace falta al menos un bloque")
        if any(not block.size for block in blocks):
            raise ValueError("un bloque vacio no puede guardar audio")
        if len({block.index for block in blocks}) != len(blocks):
            raise ValueError("un bloque no puede aparecer dos veces")
        return blocks

    def block_uses(self, kind, index):
        """Who would lose this block: commands, and speech parameters."""
        return {
            "commands": self.sound_map.commands_for(kind, index),
            "speech": list(self.speech.get((kind, index), [])),
        }

    def suggest_blocks(self, seconds, kind="dac"):
        """Blocks of one chip that together hold `seconds`, cheapest first.

        Cheapest means heard least: blocks nothing reaches go first,
        then the ones the fewest commands and speech entries use, the
        larger first so the list stays short.
        """
        rom = self._require()
        rate = self.rate(kind)
        pool = rom.dac_blocks() if kind == "dac" else rom.cvsd_blocks()
        candidates = [block for block in pool
                      if block.size and (kind, block.index) not in self.edits]

        def cost(block):
            uses = self.block_uses(kind, block.index)
            return (len(uses["commands"]) + len(uses["speech"]), -block.size)

        chosen, total = [], 0.0
        for block in sorted(candidates, key=cost):
            if total >= seconds:
                break
            chosen.append(block.index)
            total += duration(kind, block.size, rate)
        return chosen

    def music_capacity(self):
        """Per chip: how much audio it holds, and how much nothing uses.

        This is what the choice between the two comes down to -- the DAC
        sounds better and has a sixth of the room -- so the front end
        shows both numbers beside the choice rather than after it.
        """
        rom = self._require()
        capacity = {}
        for kind in self.MUSIC_KINDS:
            pool = rom.dac_blocks() if kind == "dac" else rom.cvsd_blocks()
            rate = self.rate(kind)
            blocks = [block for block in pool if block.size]
            free = [block for block in blocks
                    if not any(self.block_uses(kind, block.index).values())]
            capacity[kind] = {
                "rate": rate,
                "total_seconds": sum(duration(kind, b.size, rate) for b in blocks),
                "free_seconds": sum(duration(kind, b.size, rate) for b in free),
                "blocks": len(blocks),
            }
        return capacity

    def _music_fit(self, command, kind, indices, offset, mode):
        """The staged WAV fitted across several blocks, as one stream.

        A virtual block the size of them all goes through the same
        `prepare` a single block does, and the bytes are then dealt out
        in list order. For CVSD the decoder forgets its state in under a
        millisecond, and DAC bytes are samples, so the joins are silent.
        """
        blocks = self._music_blocks(kind, indices)
        whole = Block(index=-1, kind=kind, bank=0, start=0, end=0, chip=None,
                      offset=0, size=sum(block.size for block in blocks))
        samples, rate = self.staged[("music", command)]
        data, report = prepare(whole, samples, rate, dac_rate=DAC_RATE,
                               bit_rate=CVSD_BIT_RATE, offset=offset, mode=mode)
        pieces, at = [], 0
        for block in blocks:
            pieces.append((block, data[at:at + block.size]))
            at += block.size
        return pieces, report

    def _patch(self, command, blocks):
        return script.recording_patch(self.rom.chips["u18"], command,
                                      [block.index for block in blocks],
                                      self._program_users(), kind=blocks[0].kind,
                                      sizes=[block.size for block in blocks])

    def _music_report(self, command, blocks, report):
        patch = self._patch(command, blocks)
        result = self._trim_report(blocks[0], report)
        result.update({
            "kind": "music",
            "chip": blocks[0].kind,
            "index": command,
            "blocks": [block.index for block in blocks],
            "host": patch.host.address,
            "shared": patch.shared,
            "taken": [dict(index=block.index, **self.block_uses(block.kind, block.index))
                      for block in blocks],
        })
        return result

    def stage_music(self, command, wav_bytes, blocks=None, kind="dac"):
        """Holds a WAV against a music command; suggests blocks for it."""
        self._require()
        samples, rate = read_wav(io.BytesIO(wav_bytes))
        self.staged[("music", command)] = (samples, rate)
        if not blocks:
            blocks = self.suggest_blocks(len(samples) / float(rate), kind)
        return self.music_fit(command, blocks, kind=kind)

    def music_fit(self, command, blocks, offset=0, mode="auto", kind="dac"):
        """What replacing the music with these blocks would cost."""
        pieces, report = self._music_fit(command, kind, blocks, offset, mode)
        return self._music_report(command, [b for b, _ in pieces], report)

    def music_preview(self, command, blocks, offset=0, mode="auto", kind="dac"):
        """The replacement as the board would play it, as a WAV."""
        pieces, _ = self._music_fit(command, kind, blocks, offset, mode)
        return self._decode(kind, b"".join(data for _, data in pieces))

    def _decode(self, kind, data):
        rate = self.rate(kind)
        if kind == "dac":
            samples = decode_dac(data)
        else:
            samples = Hc55516Decoder().decode(unpack_bits(data))
            samples = to_int16(lowpass(samples, rate, RECONSTRUCTION_CUTOFF))
        buffer = io.BytesIO()
        write_wav(buffer, samples, int(rate))
        return buffer.getvalue()

    def commit_music(self, command, blocks, offset=0, mode="auto", kind="dac"):
        """Writes the recording into the blocks and repoints the music."""
        rom = self._require()
        if command in self.music_edits:
            self.revert_music(command)
        pieces, report = self._music_fit(command, kind, blocks, offset, mode)
        chosen = [block for block, _ in pieces]
        result = self._music_report(command, chosen, report)
        patch = self._patch(command, chosen)
        for block, data in pieces:
            rom.write(block, data)
            self._shapes.pop((kind, block.index), None)
            self.edits[(kind, block.index)] = report._replace(size=block.size)
        before = script.apply(rom.chips["u18"], patch.writes)
        self.music_edits[command] = {"kind": kind,
                                     "blocks": [b.index for b in chosen],
                                     "before": before, "shared": patch.shared}
        return result

    def revert_music(self, command):
        """Puts the FM back, and the borrowed blocks with it."""
        rom = self._require()
        edit = self.music_edits.pop(command, None)
        if edit is None:
            return
        self._midi_renders.pop(command, None)
        if edit["kind"] == "midi":
            for chip, offset, data in edit["before"]:
                rom.chips[chip][offset:offset + len(data)] = data
            self._shapes.pop((edit["block_kind"], edit["blocks"][0]), None)
            return
        script.apply(rom.chips["u18"], edit["before"])
        for index in edit["blocks"]:
            self.revert(edit["kind"], index)

    def _replaced_by(self, command):
        """The edit that changed this command, its own or a sharer's.

        Programs are shared, so replacing one command's music also
        replaces it in every command that runs the same programs.
        """
        if command in self.music_edits:
            return command
        for owner, edit in self.music_edits.items():
            if command in edit["shared"]:
                return owner
        return None

    def _replaced_blocks(self, command):
        owner = self._replaced_by(command)
        if owner is None:
            return None
        edit = self.music_edits[owner]
        if edit["kind"] == "midi":
            return ["MIDI \u2192 %s:%d" % (edit["block_kind"], edit["blocks"][0])]
        return ["%s:%d" % (edit["kind"], index) for index in edit["blocks"]]

    def replaced_music_wav(self, command):
        """What a replaced command now plays: its blocks, in order."""
        rom = self._require()
        edit = self.music_edits[command]
        data = b"".join(rom.extract(self.find(edit["kind"], index))
                        for index in edit["blocks"])
        return self._decode(edit["kind"], data)

    # -- music from MIDI ----------------------------------------------
    #
    # A MIDI file becomes the command's own music: each part keeps (or
    # swaps) its instrument set-up and jumps into a borrowed sample block
    # holding its notes (wpcaudio.fmcompile). The palette of instruments
    # is every set-up the game's music uses, collected once by running
    # each command (scripts/instrument_catalog.py).

    MIDI_SECONDS = 20.0
    AUDITION = [(60, 0.25), (64, 0.25), (67, 0.25), (72, 0.25), (67, 0.25),
                (64, 0.25), (60, 0.9)]

    def instruments(self):
        """The game's instrument set-ups, most used first."""
        self._require()
        if self._instruments is None:
            path = self.maps_dir / ("instrumentos_%s.json" % self.game.key)
            try:
                self._instruments = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._instruments = []
        return [{"id": e["id"], "size": len(e["bytes"]) // 2, "uses": len(e["uses"]),
                 "from": "$%02X" % e["uses"][0]["command"],
                 "channel": e["uses"][0]["channel"]} for e in self._instruments]

    def _instrument_bytes(self, ident):
        self.instruments()
        for entry in self._instruments:
            if entry["id"] == int(ident):
                return bytes.fromhex(entry["bytes"])
        raise KeyError("no hay instrumento %r" % ident)

    def _command_voices(self, command):
        """The command's voices, as the board runs them: cached."""
        if command not in self._voices:
            u18 = self.rom.chips["u18"]
            programs = {p.address for p in script.programs_of(u18, command)}
            found = [v for v in fmtrace.voices(self.rom, command) if v.address in programs]
            fm = [fmcompile.Voice(v.address, v.channel, v.prologue) for v in found if v.notes]
            self._voices[command] = {
                "fm": fm,
                "parts": fmcompile.parts(fm),
                "drums": [v.address for v in found if v.samples and not v.notes],
                "control": [v.address for v in found if not v.samples and not v.notes],
            }
        return self._voices[command]

    def _current_instrument(self, part):
        u18 = self.rom.chips["u18"]
        start = script.rom_offset(u18, part.address)
        body = bytes(u18[start:start + part.prologue - part.address]).hex()
        self.instruments()
        for entry in self._instruments:
            if entry["bytes"] == body:
                return entry["id"]
        return None

    def midi_stage(self, command, data):
        """Reads a MIDI file against a music command; describes both."""
        self._require()
        notes = midi.read(data)
        if not notes:
            raise ValueError("el MIDI no tiene notas")
        self.staged[("midi", command)] = notes
        return self.midi_info(command)

    def midi_info(self, command):
        notes = self.staged[("midi", command)]
        voices = self._command_voices(command)
        if not voices["parts"]:
            raise ValueError("este comando no tiene voces FM que sustituir")
        channels = []
        for channel in sorted({n.channel for n in notes}):
            mine = [n for n in notes if n.channel == channel]
            events = sorted([(n.start, 1) for n in mine] + [(n.start + n.length, -1) for n in mine])
            level = peak = 0
            for _, step in events:
                level += step
                peak = max(peak, level)
            channels.append({"channel": channel + 1, "notes": len(mine), "drums": channel == 9,
                             "low": min(n.pitch for n in mine), "high": max(n.pitch for n in mine),
                             "poly": peak})
        users = self._program_users()
        starts = list(users)
        parts = []
        for i, part in enumerate(voices["parts"]):
            ym = sorted(v.channel for v in voices["fm"] if v.prologue == part.prologue)
            parts.append({"index": i, "address": part.address, "ym": ym,
                          "instrument": self._current_instrument(part),
                          "room": script.room(part.address, starts)})
        # Measured, not guessed: a dry compile with the default priority,
        # plus room for a different priority to place a few more notes.
        estimate = int(fmcompile.measure(notes, voices["fm"]) * 1.15) + 32
        return {
            "kind": "midi", "index": command,
            "seconds": max(n.start + n.length for n in notes),
            "channels": channels, "parts": parts,
            "block": self.suggest_midi_block(estimate),
            "estimate": estimate,
        }

    def suggest_midi_block(self, size):
        """A block big enough for the streams, heard least."""
        rom = self._require()
        best = None
        for block in list(rom.dac_blocks()) + list(rom.cvsd_blocks()):
            if block.size < size or (block.kind, block.index) in self.edits:
                continue
            uses = self.block_uses(block.kind, block.index)
            cost = (len(uses["commands"]) + len(uses["speech"]), block.size)
            if best is None or cost < best[0]:
                best = (cost, block)
        return None if best is None else "%s:%d" % (best[1].kind, best[1].index)

    def _midi_plan(self, command, block, priority=None, instruments=None, loop=True,
                   keep_drums=False, fit="cut"):
        notes = self.staged[("midi", command)]
        kind, _, index = str(block).partition(":")
        target = self.find(kind, int(index))
        voices = self._command_voices(command)
        starts = list(self._program_users())
        chosen = {int(part): self._instrument_bytes(ident)
                  for part, ident in (instruments or {}).items() if ident not in (None, "")}
        order = [int(c) - 1 for c in priority] if priority else None
        # A block too small for the song shortens it (or thins it) to
        # fit; one with room to spare is left alone -- the closing GOTO
        # already repeats the song for as long as the command plays.
        notes, fitting = fmcompile.fit_song(notes, voices["fm"], target.size, loop=loop,
                                            priority=order, mode=fit)
        plan = fmcompile.compile_song(
            notes, voices["fm"], target, loop=loop,
            silence=voices["control"] + ([] if keep_drums else voices["drums"]),
            room=lambda address: script.room(address, starts),
            priority=order, instruments=chosen)
        self._fitting = fitting
        return plan, target, notes

    def midi_fit(self, command, block, priority=None, instruments=None, loop=True,
                 keep_drums=False, fit="cut"):
        """What compiling the staged MIDI this way would place and cost."""
        plan, target, notes = self._midi_plan(command, block, priority, instruments,
                                              loop, keep_drums, fit)
        fitting = self._fitting
        original = self.staged[("midi", command)]
        placed, lost = {}, {}
        for note in original:
            if note.channel != 9:
                placed[note.channel + 1] = placed.get(note.channel + 1, 0) + 1
        kept = {(n.channel, n.start, n.pitch) for n in notes}
        dropped = {(n.channel, n.start, n.pitch) for n in list(plan.dropped) + list(plan.out_of_range)}
        for note in original:
            key = (note.channel, note.start, note.pitch)
            if note.channel != 9 and (key not in kept or key in dropped):
                lost[note.channel + 1] = lost.get(note.channel + 1, 0) + 1
        programs = {p.address for p in script.programs_of(self.rom.chips["u18"], command)}
        users = self._program_users()
        return {
            "kind": "midi", "index": command,
            "block": "%s:%d" % (target.kind, target.index),
            "used": sum(len(data) for _, data in plan.streams), "size": target.size,
            "channels": [{"channel": c, "placed": placed[c] - lost.get(c, 0), "total": placed[c]}
                         for c in sorted(placed)],
            "dropped": len(plan.dropped), "out_of_range": len(plan.out_of_range),
            "shared": sorted({c for p in programs for c in users.get(p, ()) if c != command}),
            "taken": self.block_uses(target.kind, target.index),
            "needed": fitting["needed"], "spare": fitting["spare"], "loop": loop,
            "cut_at": fitting["cut_at"],
            "song_seconds": max(n.start + n.length for n in original),
            "removed": [c + 1 for c in fitting["removed"]],
        }

    def _apply_midi(self, rom, plan, target):
        """Writes a plan into `rom`; returns what it overwrote."""
        before = []
        u18 = rom.chips["u18"]
        for address, data in plan.writes:
            offset = script.rom_offset(u18, address)
            before.append(("u18", offset, bytes(u18[offset:offset + len(data)])))
            u18[offset:offset + len(data)] = data
        chip, offset = rom.resolve(target.bank, target.start)
        image = rom.chips[chip]
        before.append((chip, offset, bytes(image[offset:offset + target.size])))
        silence = 0x80 if target.kind == "dac" else 0x55
        image[offset:offset + target.size] = bytes([silence]) * target.size
        for address, data in plan.streams:
            chip, offset = rom.resolve(target.bank, address)
            rom.chips[chip][offset:offset + len(data)] = data
        return before

    def _note(self, stage, done, total, command):
        self.progress = {"command": command, "stage": stage,
                         "done": round(done, 2), "total": total}

    def _render(self, rom, command, seconds, name):
        target = self.cache_dir / name
        try:
            path = render_rom(rom, command, seconds, target,
                              on_progress=lambda s, d, t: self._note(s, d, t, command))
        finally:
            self._note("done", seconds, seconds, command)
        return None if path is None else path.read_bytes()

    def midi_preview(self, command, block, priority=None, instruments=None, loop=True,
                     keep_drums=False, fit="cut"):
        """The compiled song as the board plays it, without committing."""
        plan, target, notes = self._midi_plan(command, block, priority, instruments,
                                              loop, keep_drums, fit)
        trial = SoundRom(**dict({k: bytes(v) for k, v in self.rom.chips.items()},
                                **self.game.directory))
        self._apply_midi(trial, plan, target)
        seconds = min(self.MIDI_SECONDS, max(n.start + n.length for n in notes) + 1)
        return self._render(trial, command, seconds, "midi_preview_%02X.wav" % command)

    def commit_midi(self, command, block, priority=None, instruments=None, loop=True,
                    keep_drums=False, fit="cut"):
        """Compiles the staged MIDI into the command, for real."""
        rom = self._require()
        if command in self.music_edits:
            self.revert_music(command)
        result = self.midi_fit(command, block, priority, instruments, loop, keep_drums, fit)
        plan, target, _ = self._midi_plan(command, block, priority, instruments,
                                          loop, keep_drums, fit)
        before = self._apply_midi(rom, plan, target)
        self._shapes.pop((target.kind, target.index), None)
        self.music_edits[command] = {"kind": "midi", "block_kind": target.kind,
                                     "blocks": [target.index], "before": before,
                                     "shared": result["shared"]}
        self._midi_renders.pop(command, None)
        return result

    def _render_midi_edit(self, command, seconds):
        owner = self._replaced_by(command)
        if command not in self._midi_renders:
            notes = self.staged.get(("midi", owner))
            length = (min(self.MIDI_SECONDS, max(n.start + n.length for n in notes) + 1)
                      if notes else seconds)
            self._midi_renders[command] = self._render(
                self.rom, command, length, "midi_edit_%02X.wav" % command)
        return self._midi_renders[command]

    def instrument_wav(self, command, ident):
        """A short arpeggio on one instrument, played through `command`."""
        self._require()
        target = self.cache_dir / ("instrumento_%s_%d_v1.wav" % (self.game.key, int(ident)))
        if target.exists():
            return target.read_bytes()
        voices = self._command_voices(command)
        starts = list(self._program_users())
        code = self._instrument_bytes(ident)
        parts = voices["parts"]
        usable = [i for i, part in enumerate(parts)
                  if script.room(part.address, starts) >= len(code) + 4]
        if not usable:
            raise ValueError("ninguna parte de este comando tiene sitio para ese instrumento")
        part = usable[0]
        notes, at = [], 0.0
        for pitch, length in self.AUDITION:
            notes.append(midi.Note(at, length * 0.9, pitch, 100, 0))
            at += length
        kind, _, index = self.suggest_midi_block(256).partition(":")
        block = self.find(kind, int(index))
        plan = fmcompile.compile_song(
            notes, [p for i, p in enumerate(parts) if i == part], block, loop=False,
            silence=voices["control"] + voices["drums"] +
            [p.prologue for i, p in enumerate(parts) if i != part],
            room=lambda address: script.room(address, starts), instruments={0: code})
        trial = SoundRom(**dict({k: bytes(v) for k, v in self.rom.chips.items()},
                                **self.game.directory))
        self._apply_midi(trial, plan, block)
        data = self._render(trial, command, at + 0.6, target.name)
        if data is None:
            raise ValueError("el instrumento no produjo sonido")
        return data

    # -- DCS ---------------------------------------------------------
    #
    # Nothing to reverse-engineer: DCS Explorer lists and decodes, DCS
    # Encoder rebuilds (wpcaudio.dcs). A replacement re-programs the
    # whole track around one new stream -- the old streams stay, because
    # other tracks may share them -- at the best quality that still fits
    # the set's own chips.

    def _load_dcs(self):
        game = self.game
        images = {socket: self.images[socket] for socket in game.roms}
        self.rom = None
        self.dcs = dcs.DcsSet(images, game.files, self.cache_dir / "dcs")
        self.dcs_patched = None
        self.dcs_edits = {}
        self.dcs_bitrate = None
        self.notes = self._read_notes()
        self.dcs.catalog()
        return game

    def _require_dcs(self):
        if self.dcs is None:
            raise NotLoaded("todavia no hay un juego DCS identificado")
        return self.dcs

    def view(self):
        """What the page draws after loading: chips, or a DCS track list."""
        return self.dcs_map() if self.platform == "dcs" else self.chip_map()

    def dcs_map(self):
        base = self._require_dcs()
        tracks, plays, streams = base.catalog()
        users = {}
        for command, played in plays.items():
            for stream in played:
                users.setdefault(stream, set()).add(command)
        rows = []
        for track in tracks:
            played = plays.get(track.command, [])
            shared = sorted({c for s in played for c in users.get(s, ()) if c != track.command})
            note = self.note_for("dcs", track.command)
            rows.append({
                "command": track.command, "label": dcs.label(track.command),
                "channel": track.channel, "seconds": track.ms / 1000.0, "loop": track.loop,
                "streams": len(played), "shared": [dcs.label(c) for c in shared],
                "audio": bool(played),
                "edited": track.command in self.dcs_edits,
                "note": note.get("note", ""), "category": note.get("category"),
                "colour": note.get("colour") or CATEGORIES.get(note.get("category")),
                "marked": bool(note.get("marked")),
            })
        # The same chip drawing as the pre-DCS map: every stream at its
        # real place in its ROM. Decoration first, but true decoration --
        # it is where the audio physically is.
        chips = {socket: [] for socket in base.names}
        for address, stream in sorted(streams.items()):
            socket, offset = dcs.stream_place(address)
            if socket in chips:
                chips[socket].append({
                    "kind": "dcs", "index": address, "offset": offset, "size": stream.size,
                    "seconds": stream.seconds,
                    "note": "%s · %s" % ("$%06X" % address, ", ".join(
                        dcs.label(c) for c in sorted(users.get(address, ()))) or "-"),
                    "tracks": sorted(users.get(address, ())),
                    "edited": False, "marked": False,
                })
        chip_rows = [{"name": socket, "size": len(base.images[socket]),
                      "pages": len(base.images[socket]) // PAGE_SIZE,
                      "covered": sum(b["size"] for b in blocks), "blocks": blocks}
                     for socket, blocks in chips.items()]
        for row in rows:
            row["stream_ids"] = plays.get(row["command"], [])
        return {
            "platform": "dcs",
            "chips": chip_rows,
            "game": {"key": self.game.key, "name": self.game.name,
                     "ipdb": ipdb_url(self.game.key), "flyers": flyer_url(self.game.key)},
            "rate": dcs.NATIVE_RATE,
            "tracks": rows,
            "bitrate": self.dcs_bitrate,
            "edits": [dcs.label(c) for c in sorted(self.dcs_edits)],
            "marked": sorted(key for key, n in self.notes.items() if n.get("marked")),
            "categories": CATEGORIES,
        }

    def dcs_wav(self, command, original=False):
        source = self._require_dcs() if original or self.dcs_patched is None else self.dcs_patched
        return source.track_wav(command).read_bytes()

    def _dcs_audio(self, command, wav_bytes, match=True):
        """The user's selection as the board will take it: 31.25 kHz mono.

        `match` brings it to the loudness of the track it replaces, so a
        voice line does not jump out of (or vanish under) the mix the
        game was balanced for.
        """
        samples, rate = read_wav(io.BytesIO(wav_bytes))
        audio = resample(samples, rate, dcs.NATIVE_RATE).astype(float)
        if match:
            original, _ = read_wav(io.BytesIO(self.dcs_wav(command, original=True)))
            gain = _loudness(original) / max(_loudness(audio), 1.0)
            audio = audio * gain
        out = io.BytesIO()
        write_wav(out, to_int16(np.clip(audio, -32767, 32767)), dcs.NATIVE_RATE)
        return out.getvalue()

    def _dcs_build(self, edits):
        base = self._require_dcs()
        if not edits:
            return None, None
        work = self.cache_dir / "dcs" / ("parche_%s" % base.key)
        images, rate = base.patch_fitting({}, work, tracks=edits)
        return dcs.DcsSet(images, base.names, self.cache_dir / "dcs"), rate

    def dcs_preview(self, command, wav_bytes, match=True):
        """The selection encoded and decoded again: what the ROM will play."""
        edits = dict(self.dcs_edits)
        edits[command] = self._dcs_audio(command, wav_bytes, match)
        trial, rate = self._dcs_build(edits)
        return trial.track_wav(command).read_bytes(), {"bitrate": rate}

    def dcs_commit(self, command, wav_bytes, match=True):
        audio = self._dcs_audio(command, wav_bytes, match)
        edits = dict(self.dcs_edits)
        edits[command] = audio
        patched, rate = self._dcs_build(edits)
        self.dcs_edits, self.dcs_patched, self.dcs_bitrate = edits, patched, rate
        samples, _ = read_wav(io.BytesIO(audio))
        return {"command": dcs.label(command), "bitrate": rate,
                "seconds": len(samples) / float(dcs.NATIVE_RATE)}

    def dcs_revert(self, command):
        edits = dict(self.dcs_edits)
        edits.pop(command, None)
        patched, rate = self._dcs_build(edits)
        self.dcs_edits, self.dcs_patched, self.dcs_bitrate = edits, patched, rate

    def _dcs_zip(self):
        source = self.dcs_patched or self.dcs
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for socket, name in sorted(source.names.items()):
                archive.writestr(name, source.images[socket])
            lines = [self.game.name, ""]
            if not self.dcs_edits:
                lines.append("Sin cambios: las imagenes son identicas a las originales.")
            for command in sorted(self.dcs_edits):
                lines.append("pista %s  reprogramada con audio nuevo" % dcs.label(command))
            if self.dcs_edits:
                lines += ["", "Codificado por DCS Encoder a BitRate=%d, la mejor calidad que "
                          "cabe en los %d chips originales." % (self.dcs_bitrate, len(source.names)),
                          "Los nombres son los del romset %s de PinMAME." % self.game.pinmame]
            archive.writestr("README.txt", "\n".join(lines))
            if self.notes:
                archive.writestr("notas.json", json.dumps(self.notes, ensure_ascii=False, indent=1))
        return buffer.getvalue()

    # -- getting it back out ----------------------------------------

    def patched(self):
        """The edited images, as a zip. Only what changed is renamed."""
        if self.platform == "dcs":
            return self._dcs_zip()
        rom = self._require()
        # The board checksums every page at power-on, so the copy that
        # leaves has its sums put right; the working images are left
        # alone so that a revert still compares against the original.
        export = SoundRom(**dict(rom.chips, **self.game.directory))
        fixed = export.fix_checksums()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for socket, image in sorted(export.chips.items()):
                archive.writestr("%s_%s.rom" % (self.game.key, socket), bytes(image))
            archive.writestr("README.txt", self._summary(fixed))
            if self.notes:
                archive.writestr("notas.json",
                                 json.dumps(self.notes, ensure_ascii=False, indent=1))
        return buffer.getvalue()

    def wav_bundle(self, only_marked=False):
        """Every sound as a WAV in one zip, named so they can be found.

        The point of marking blocks is to work on a handful out of two
        hundred, so the same bundle serves both: all of them, or just
        the ones the user flagged.
        """
        rom = self._require()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for block in rom.blocks():
                if not block.size:
                    continue
                note = self.note_for(block.kind, block.index)
                if only_marked and not note.get("marked"):
                    continue
                archive.writestr(self._wav_name(block, note.get("note")),
                                 self.block_wav(block.kind, block.index))
            archive.writestr("notas.json",
                             json.dumps(self.notes, ensure_ascii=False, indent=1))
        return buffer.getvalue()

    @staticmethod
    def _wav_name(block, note):
        name = "%s_%03d" % (block.kind, block.index)
        if note:
            # Keep the user's wording but make it a filename.
            safe = re.sub(r"[^\w\-]+", "_", note, flags=re.UNICODE).strip("_")
            if safe:
                name += "_" + safe[:60]
        return name + ".wav"

    def _summary(self, fixed=()):
        lines = ["%s" % self.game.name, ""]
        if not self.edits:
            lines.append("Sin cambios: las imagenes son identicas a las originales.")
        for command, edit in sorted(self.music_edits.items()):
            if edit["kind"] == "midi":
                lines.append("musica $%02X  compilada desde MIDI, notas en %s:%d"
                             % (command, edit["block_kind"], edit["blocks"][0]))
            else:
                lines.append("musica $%02X  ahora reproduce %s %s"
                             % (command, edit["kind"],
                                ", ".join(str(i) for i in edit["blocks"])))
            if edit["shared"]:
                lines.append("    comparte programas con %s, que tambien cambian"
                             % ", ".join("$%02X" % c for c in edit["shared"]))
        for (kind, index), fit in sorted(self.edits.items()):
            lines.append("%s:%d  %d bytes" % (kind, index, fit.size))
            if fit.truncated:
                lines.append("    se recortaron %d bytes del final" % fit.truncated)
            if fit.padded:
                lines.append("    se rellenaron %d bytes de silencio" % fit.padded)
        if fixed:
            lines.append("")
            lines.append("Checksum de arranque corregido en %d paginas (byte $BFFF): %s"
                         % (len(fixed), ", ".join("$%02X" % b for b in fixed)))
        lines.append("")
        lines.append("Cada bloque conserva su tamano original, asi que las tablas de "
                     "la ROM siguen siendo validas y el resto de la imagen no se toca.")
        return "\n".join(lines)
