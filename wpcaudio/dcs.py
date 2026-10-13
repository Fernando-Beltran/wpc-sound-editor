"""DCS sound ROMs, through DCS Explorer and DCS Encoder.

Pre-DCS boards had to be reverse-engineered before anything could be
edited. DCS does not: Michael J Roberts' DCS Explorer decodes it
natively (validated bit for bit against PinMAME on every title) and his
DCS Encoder rebuilds whole ROM sets, patching only what a script says.
Both are BSD-3 command-line programs that run as separate processes, so
this module only drives them and reads what they print.

Three words, kept apart because the format keeps them apart:

- a **track** is what a command code starts: a small program on one of
  four channels (`SetMixingLevel`, `Play(stream $14031A)`, `Wait(676)`);
- a **stream** is the compressed audio a track plays;
- streams are **shared** -- `$14031A` is played by track `$000F` on
  channel 0 and by `$001A` on channel 3 -- so replacing one changes
  every track that plays it, and the editor has to say so.

Both tools take a PinMAME-style zip, so the images are zipped under the
names the game's romset uses before anything runs.
"""

import hashlib
import io
import pathlib
import re
import subprocess
import zipfile
from collections import namedtuple

Track = namedtuple("Track", "command channel kind ms loop address")
Stream = namedtuple("Stream", "address seconds size")

NATIVE_RATE = 31250            # DCS plays at 31.25 kHz

_TRACK = re.compile(r"^([0-9a-f]{2}) ([0-9a-f]{2})\s+(\d+)\s+\d+\s+(\w+)\s+([\d.]+)ms(\(loop\))?\s+([0-9A-Fa-f]+)",
                    re.I)
_PROGRAM = re.compile(r"^Track \$([0-9a-f]{4}) Channel (\d+)", re.I)
_PLAY = re.compile(r"Play\(stream \$([0-9a-f]+)", re.I)
_STREAM = re.compile(r"^([0-9a-f]{7}) \[U\d [0-9a-f]+\]\s+\d+\s+(?:[0-9a-f]{2} ){16}\s*([\d.]+)\s+(\d+)",
                     re.I)


class ToolMissing(Exception):
    """DCS Explorer or DCS Encoder is not where this expects it."""


class ToolFailed(Exception):
    """One of the tools ran and said no."""


class DoesNotFit(Exception):
    """Even the lowest quality needs more chips than the set has."""


# Encoder bit rates to try, best first. 48000 is the encoder's floor.
# The figure is a ceiling hint, not a rate: STTNG's own narration runs at
# about 21 kbit/s, and a 20 s phrase at 56000 comes out near 15.5.
BITRATES = (128000, 96000, 80000, 64000, 56000, 48000)


def _tools():
    try:
        from wpcide import paths
        return paths.bundled("tools", "dcsexplorer")
    except Exception:          # noqa: BLE001
        return pathlib.Path(__file__).resolve().parent.parent / "tools" / "dcsexplorer"


EXPLORER = _tools() / "DCSExplorer.exe"
ENCODER = _tools() / "DCSEncoder.exe"


# -- reading what DCS Explorer prints -------------------------------------


def parse_tracks(text):
    """`--tracks`: one Track per line of the index."""
    tracks = []
    for line in text.splitlines():
        match = _TRACK.match(line.strip())
        if match:
            hi, lo, channel, kind, ms, loop, address = match.groups()
            tracks.append(Track((int(hi, 16) << 8) | int(lo, 16), int(channel), kind,
                                float(ms), bool(loop), int(address, 16)))
    return tracks


def parse_programs(text):
    """`--programs`: the streams each track plays, in order, no repeats."""
    plays, current = {}, None
    for line in text.splitlines():
        head = _PROGRAM.match(line.strip())
        if head:
            current = int(head.group(1), 16)
            plays[current] = []
            continue
        if current is None:
            continue
        for address in _PLAY.findall(line):
            value = int(address, 16)
            if value not in plays[current]:
                plays[current].append(value)
    return plays


def parse_streams(text):
    """`--streams`: every stream's address, length and size in ROM."""
    streams = {}
    for line in text.splitlines():
        match = _STREAM.match(line.strip())
        if match:
            address, seconds, size = match.groups()
            streams[int(address, 16)] = Stream(int(address, 16), float(seconds), int(size))
    return streams


def parse_bodies(text):
    """`--programs`: each track's channel and statements, comments gone."""
    bodies, current = {}, None
    for line in text.splitlines():
        head = _PROGRAM.match(line.strip())
        if head:
            current = int(head.group(1), 16)
            bodies[current] = (int(head.group(2)), [])
            continue
        if current is None:
            continue
        statement = line.split("//")[0].strip()
        if statement == "};":
            current = None
        elif statement:
            bodies[current][1].append(statement)
    return bodies


_WAIT = re.compile(r"^Wait\(\d+\)\s*")


def track_program(command, channel, statements, stream_file, params=""):
    """A new program for a track: its own set-up, one stream, its own close.

    Everything the original did before its first `Play` -- its mixing
    level, ducking the music on channel 0 -- comes first; then the new
    stream, waited out whole; then whatever the original did after its
    last `Play`, minus the wait that timed the old audio (restoring the
    music's level, say). So the track keeps behaving like itself around
    audio of any length, and the streams it used to play stay put for
    the other tracks that share them.
    """
    plays = [i for i, s in enumerate(statements) if "Play(" in s]
    if not plays:
        raise ValueError("la pista %s no reproduce ningun stream" % label(command))
    before = statements[:plays[0]]
    after = statements[plays[-1] + 1:]
    if after:
        after = [_WAIT.sub("", after[0])] + after[1:]
    after = [s for s in after if s and s != "End;"]
    lines = ["Track $%04X channel %d {" % (command, channel)]
    lines += ["    " + s for s in before]
    lines += ['    Play(Stream "%s"%s);' % (stream_file, " (%s)" % params if params else ""),
              "    Wait(stream);"]
    lines += ["    " + s for s in after]
    lines.append("};")
    return "\n".join(lines)


def stream_place(address):
    """Which chip a stream lives in, and where: (socket, offset).

    A stream address is the chip's slot in its top digit -- the sound
    ROMs sit 1 MB apart, U2 first -- and the offset within the chip
    below it: `$14031A` is U3 at `0x4031A`.
    """
    return "u%d" % ((address >> 20) + 2), address & 0xFFFFF


def label(command):
    return "%02X %02X" % (command >> 8, command & 0xFF)


# -- driving the tools ----------------------------------------------------


def zip_images(images, names):
    """The images as a PinMAME romset zip, under the names it expects."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for socket, data in sorted(images.items()):
            archive.writestr(names[socket], bytes(data))
    return buffer.getvalue()


def _run(exe, args, cwd, timeout=600):
    if not pathlib.Path(exe).exists():
        raise ToolMissing("no encuentro %s" % exe)
    done = subprocess.run([str(exe)] + [str(a) for a in args], cwd=str(cwd),
                          capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise ToolFailed((done.stdout + done.stderr).strip()[-600:] or "fallo sin mensaje")
    return done.stdout


class DcsSet:
    """One set of DCS images, with the tools' answers about it cached.

    Everything is keyed by a hash of the images, so the original set and
    every patched version get their own folder of listings and WAVs.
    """

    def __init__(self, images, names, work):
        self.images = {k: bytes(v) for k, v in images.items()}
        self.names = dict(names)
        digest = hashlib.sha1()
        for socket in sorted(self.images):
            digest.update(self.images[socket])
        self.key = digest.hexdigest()[:12]
        self.folder = pathlib.Path(work) / self.key
        self.folder.mkdir(parents=True, exist_ok=True)
        self.zip = self.folder / "set.zip"
        if not self.zip.exists():
            self.zip.write_bytes(zip_images(self.images, self.names))
        self._catalog = None

    def _listing(self, flag):
        cached = self.folder / ("%s.txt" % flag)
        if not cached.exists():
            cached.write_text(_run(EXPLORER, ["--info", "--" + flag, self.zip.name],
                                   self.folder), encoding="utf-8")
        return cached.read_text(encoding="utf-8")

    def catalog(self):
        """(tracks, plays, streams) -- the whole map of the set."""
        if self._catalog is None:
            self._catalog = (parse_tracks(self._listing("tracks")),
                             parse_programs(self._listing("programs")),
                             parse_streams(self._listing("streams")))
        return self._catalog

    def track_wav(self, command):
        """One track decoded to WAV; the whole set is extracted once."""
        tracks = self.folder / "tracks"
        done = tracks / ".completo"
        if not done.exists():
            # A marker, not the folder: an extraction that failed half
            # way must not pass for a finished one next time.
            tracks.mkdir(exist_ok=True)
            _run(EXPLORER, ["--silent", "--terse", "--extract-tracks=t",
                            str(self.zip.resolve())], tracks)
            done.write_text("ok")
        path = tracks / ("t_%04x.wav" % command)
        if not path.exists():
            raise KeyError("la pista %s no tiene audio" % label(command))
        return path

    def bodies(self):
        return parse_bodies(self._listing("programs"))

    def patch(self, replacements, work, tracks=None, params="", extra=""):
        """A new image set with these streams replaced.

        `replacements` maps stream address to WAV bytes. The encoder
        rebuilds every chip, but copies untouched streams raw, so they
        decode exactly as before; the chip names and sizes are kept so
        PinMAME will still load the result.
        """
        work = pathlib.Path(work)
        work.mkdir(parents=True, exist_ok=True)
        lines = ["// Generado por el editor WPC: streams sustituidos."]
        for address, data in sorted(replacements.items()):
            name = "s_%06x.wav" % address
            (work / name).write_bytes(data)
            lines.append('Stream S%06X "%s"%s replaces $%06X;'
                         % (address, name, " (%s)" % params if params else "", address))
        # Whole tracks re-programmed around a new stream of their own:
        # {command: wav bytes}. The old streams stay for anyone sharing.
        bodies = self.bodies() if tracks else {}
        for command, data in sorted((tracks or {}).items()):
            name = "t_%04x.wav" % command
            (work / name).write_bytes(data)
            channel, statements = bodies[command]
            lines.append(track_program(command, channel, statements, name, params))
        if extra:
            lines.append(extra)
        (work / "parche.dcs").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (work / "origen.zip").write_bytes(self.zip.read_bytes())
        output = work / "parcheado.zip"
        if output.exists():
            output.unlink()
        _run(ENCODER, ["--patch", "--rom-size=*", "--rom-prefix=*", "-q",
                       "-o", output.name, "origen.zip", "parche.dcs"], work)
        with zipfile.ZipFile(output) as archive:
            found = {name.lower(): archive.read(name) for name in archive.namelist()}
        mine = {name.lower() for name in self.names.values()}
        extra = sorted(name for name in found if name not in mine and
                       re.search(r"u\d", name))
        images = {socket: found[name.lower()] for socket, name in self.names.items()
                  if name.lower() in found}
        return images, extra

    def patch_fitting(self, replacements, work, tracks=None, extra=""):
        """The best quality whose result still fits the set's own chips.

        The board has the sockets it has: a result that needs an extra
        chip (the encoder adds U9 when the audio overflows U8) cannot go
        in the machine, and PinMAME does not know the file either. So
        this walks down the bit rates until the new audio fits, and says
        which one it used.
        """
        sizes = {socket: len(data) for socket, data in self.images.items()}
        for rate in BITRATES:
            images, more = self.patch(replacements, work, tracks, "BitRate=%d" % rate, extra)
            if not more and {k: len(v) for k, v in images.items()} == sizes:
                return images, rate
        raise DoesNotFit("no cabe ni a la calidad minima: hace falta un chip mas")
