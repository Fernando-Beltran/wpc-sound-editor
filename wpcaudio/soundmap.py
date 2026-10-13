"""Which command plays which sound.

The game's CPU asks for a sound by writing one byte. What that byte
plays is not written down anywhere in the ROM in a form that can be
read off: it goes through a command table, a script, and an interpreter
whose opcodes took a long time to half-understand. Walking that
statically produced confident wrong answers twice.

So this is built by observation instead. `scripts/scan_commands.py`
runs every command byte on the emulated board and records the sample
streams the program actually starts, matching each against the sample
directory. The result is a plain list of rows, one per command, which
this module indexes both ways: a command's sounds, and a sound's
commands.

A row that names a stream the directory does not contain is counted,
not dropped. A non-zero `unknown_starts` means the watcher or the
directory disagrees with the program about something, and hiding it
would turn a bug into a silence.
"""

import json
from collections import OrderedDict


def _split(label):
    kind, _, index = label.partition(":")
    return kind, int(index)


class SoundMap:
    """A command-to-sound index, built from a scan."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self._by_command = OrderedDict()
        self._by_block = {}
        self.unknown_starts = 0
        self.failed = []

        for row in self.rows:
            command = row.get("cmd")
            if "error" in row:
                self.failed.append(command)
                continue
            sounds = [_split(label) for label in row.get("samples") or []]
            self._by_command[command] = {
                "sounds": sounds,
                "keyon": row.get("keyon", 0),
                "writes": row.get("writes", 0),
            }
            self.unknown_starts += row.get("unknown", 0)
            for block in sounds:
                self._by_block.setdefault(block, [])
                if command not in self._by_block[block]:
                    self._by_block[block].append(command)

    # -- building ---------------------------------------------------

    @classmethod
    def from_scan(cls, rows):
        return cls(rows)

    @classmethod
    def load(cls, path):
        """Reads a scan result. A missing file is an empty map.

        The editor should work on a ROM nobody has scanned yet; it just
        cannot say what triggers each sound.
        """
        try:
            with open(str(path), encoding="utf-8") as handle:
                return cls(json.load(handle))
        except (OSError, ValueError):
            return cls()

    @property
    def is_empty(self):
        return not self._by_command

    # -- asking it things -------------------------------------------

    def commands_for(self, kind, index):
        return list(self._by_block.get((kind, index), []))

    def sounds_for(self, command):
        entry = self._by_command.get(command)
        return list(entry["sounds"]) if entry else []

    def is_music(self, command):
        entry = self._by_command.get(command)
        return bool(entry and entry["keyon"])

    def is_silent(self, command):
        entry = self._by_command.get(command)
        return bool(entry and not entry["keyon"] and not entry["sounds"])

    def coverage(self, blocks):
        """How much of the directory the scan actually reached."""
        blocks = [tuple(block) for block in blocks]
        reached = [block for block in blocks if block in self._by_block]
        return {
            "total": len(blocks),
            "reached": len(reached),
            "unreached": [block for block in blocks if block not in self._by_block],
        }
