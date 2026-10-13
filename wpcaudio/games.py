"""Knowing which machine a set of sound ROM images belongs to.

Identification is by CRC32 of each image, which is also the import
check: the tooling never ships a ROM, so the only thing it can do is
confirm that the dump the user supplied is the one the sound map was
built against. A mismatch means the map's offsets do not apply.

**Where the bytes come from is deliberately not this module's job.**
The images are passed in. A front end may let the user drop a file, or
open the machine's page on IPDB so the user downloads it themselves --
IPDB's `robots.txt` is `User-agent: * / Disallow: /`, so nothing here
fetches from them automatically. Hooking up an authorised download
later means adding a source, not changing any of this.
"""

import zlib
from collections import namedtuple
from urllib.parse import quote_plus

SocketReport = namedtuple("SocketReport", "socket expected found ok")

# One entry per sound ROM set. `roms` is the CRC32 of each socket and
# `sizes` the image length, both of which the import check confirms.
# `directory` carries the per-game constants `wpcaudio.soundrom` needs:
# which bank holds the sample directory and where its two table
# pointers sit in that bank's header. They come from disassembling the
# game's own program ROM and are different for every machine.
GAMES = {
    "tz_l2": {
        "name": "Twilight Zone (Bally, 1993) -- sound L-2",
        "ipdb": 2684,
        "roms": {"u18": 0x66575EC2, "u15": 0x389D2442, "u14": 0x5A67BD56},
        "sizes": {"u18": 0x80000, "u15": 0x80000, "u14": 0x20000},
        "directory": {"directory_bank": 0x7C, "dac_table": 0x4003, "cvsd_table": 0x4015},
    },
    "tz_l1": {
        "name": "Twilight Zone (Bally, 1993) -- sound L-1",
        "ipdb": 2684,
        # U14 and U15 are pure data and identical across revisions;
        # only the program ROM changed.
        "roms": {"u18": 0xA021A494, "u15": 0x389D2442, "u14": 0x5A67BD56},
        "sizes": {"u18": 0x80000, "u15": 0x80000, "u14": 0x20000},
        "directory": {"directory_bank": 0x7C, "dac_table": 0x4003, "cvsd_table": 0x4015},
    },
    # DCS: seven sound ROMs, U2-U8, and no sample directory to locate --
    # DCS Explorer reads the set's own catalogue. `files` are the names
    # the PinMAME romset uses, which both DCS tools expect inside a zip.
    "sttng_l1": {
        "name": "Star Trek: The Next Generation (Williams, 1993) -- sound L-1",
        "ipdb": 2357,
        "platform": "dcs",
        "pinmame": "sttng_l7",
        "roms": {"u2": 0xC3BD7BF5, "u3": 0x9456CAC7, "u4": 0x179D22A4, "u5": 0x231A3E72,
                 "u6": 0xBB21377D, "u7": 0xD81B39F0, "u8": 0xC9FB065E},
        "sizes": {"u%d" % u: 0x80000 for u in range(2, 9)},
        "files": {"u%d" % u: "ng_u%d_s.l1" % u for u in range(2, 9)},
        "directory": {},
    },
    "sttng_x7": {
        "name": "Star Trek: The Next Generation (Williams, 1993) -- special sound LX-7",
        "ipdb": 2357,
        "platform": "dcs",
        "pinmame": "sttng_x7",
        "roms": {"u2": 0xE9FE68FE, "u3": 0x368CFD89, "u4": 0x8E79A513, "u5": 0x46049EB0,
                 "u6": 0xE0124DA0, "u7": 0xDC1C74D0, "u8": 0xC9FB065E},
        "sizes": {"u%d" % u: 0x80000 for u in range(2, 9)},
        "files": dict({"u%d" % u: "ngs_u%d.rom" % u for u in range(2, 8)}, u8="ng_u8_s.l1"),
        "directory": {},
    },
}

# The board a set belongs to: pre-DCS WPC sound (YM2151, HC55516 and an
# 8-bit DAC behind a 6809) unless an entry says otherwise.
PRE_DCS = "wpcs"

Game = namedtuple("Game", "key name ipdb roms sizes directory platform files pinmame")


def crc32(data):
    return zlib.crc32(bytes(data)) & 0xFFFFFFFF


def _as_game(key):
    entry = GAMES[key]
    return Game(key=key, name=entry["name"], ipdb=entry["ipdb"],
                roms=entry["roms"], sizes=entry["sizes"],
                directory=entry["directory"],
                platform=entry.get("platform", PRE_DCS),
                files=entry.get("files", {}), pinmame=entry.get("pinmame"))


def identify(images):
    """Returns the Game whose every socket matches, or None.

    Every socket the game lists has to be present and correct. A
    partial set is not an identification: the sample directory spans
    all three chips, so a map built on two of them would point at
    nothing for a third of its blocks.
    """
    found = {socket: crc32(data) for socket, data in images.items()}
    for key, entry in GAMES.items():
        if all(found.get(socket) == expected
               for socket, expected in entry["roms"].items()):
            return _as_game(key)
    return None


def candidate(images, platform=None):
    """The game the images look like so far, for showing its sockets.

    Before a set is complete nothing is identified, but the page still
    has to draw the right sockets -- three for Twilight Zone, seven for
    a DCS game. The best guess is the game with the most CRCs matched,
    then the most of its sockets filled; with nothing loaded, the first.
    """
    found = {socket: crc32(data) for socket, data in images.items()}

    def score(key):
        roms = GAMES[key]["roms"]
        return (sum(found.get(s) == c for s, c in roms.items()),
                sum(s in found for s in roms))

    best = max(GAMES, key=score)
    if score(best) != (0, 0):
        return _as_game(best)
    # Nothing to go on yet: the board the user said they have, if any.
    for key, entry in GAMES.items():
        if entry.get("platform", PRE_DCS) == (platform or PRE_DCS):
            return _as_game(key)
    return _as_game(next(iter(GAMES)))


def socket_for(data):
    """Which socket of which known game an image is, by its CRC alone."""
    crc = crc32(data)
    for key, entry in GAMES.items():
        for socket, expected in entry["roms"].items():
            if expected == crc:
                return socket
    return None


def verify(images, key):
    """Reports each socket of `key` against what the user supplied."""
    entry = GAMES[key]
    found = {socket: crc32(data) for socket, data in images.items()}
    return {
        socket: SocketReport(socket=socket, expected=expected,
                             found=found.get(socket),
                             ok=found.get(socket) == expected)
        for socket, expected in entry["roms"].items()
    }


def ipdb_url(key):
    """The machine's page, for a front end to open in the user's browser."""
    return "https://www.ipdb.org/machine.cgi?id=%d" % GAMES[key]["ipdb"]


def ipdb_search_url(term):
    """IPDB's own search, for a machine we do not have on file.

    Opening their page in the user's browser is the one thing IPDB
    explicitly invites -- they publish a page about linking to them --
    while their `robots.txt` is `Disallow: /` for automated agents. So
    the search happens there, with a person clicking, and the file
    comes back to the editor by being dropped on it.
    """
    return ("https://www.ipdb.org/search.pl?any=%s&search=Search+Database"
            "&searchtype=quick" % quote_plus(term or ""))


# The Arcade Flyer Archive, which hosts the machines' advertising
# flyers. Their robots.txt ends `User-agent: * / Disallow: /` under a
# heading they wrote as "Default Behavior (NOT OK)", so nothing here
# fetches from them either: the editor links to the gallery and the
# user saves the image they want.
FLYERS = "https://flyers.arcade-museum.com/pinballs"


def flyer_url(key):
    """Their pinball gallery, on the letter the machine starts with."""
    name = GAMES[key]["name"].strip()
    # `lstrip("The ")` would strip *characters*, not the prefix: it
    # turns "Twilight Zone" into "wilight Zone".
    for article in ("The ", "A "):
        if name.startswith(article):
            name = name[len(article):]
            break
    initial = name[0].upper()
    if not initial.isalpha():
        initial = "0-9"
    return "%s/gallery/%s" % (FLYERS, initial)


def search(term):
    """Known games whose name matches, for the front end's own list."""
    needle = (term or "").strip().lower()
    found = []
    for key in GAMES:
        game = _as_game(key)
        if not needle or needle in game.name.lower() or needle in key:
            found.append({"key": key, "name": game.name, "url": ipdb_url(key),
                          "sockets": sorted(game.roms)})
    return found
