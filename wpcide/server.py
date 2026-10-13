"""A local HTTP front end for the sound editor.

Nothing here is a web service. It binds to the loopback address, serves
one page, and exposes `wpcide.app.Ide` over a handful of JSON calls so
the editor can be a browser window instead of a terminal. The ROM never
leaves the machine: it is read by the page the user has open and handed
to a process on the same computer.

The transport is deliberately thin. When this moves to a browser build
the page stays and these handlers go away, because Pyodide can call
`Ide` directly.
"""

import json
import mimetypes
import pathlib
import re
import socket
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from wpcaudio.fetcher import FetchError, fetch, save_settings, settings
from wpcide import paths
from wpcaudio.games import GAMES, ipdb_search_url, search
from wpcide.app import Ide, NotLoaded

STATIC = paths.bundled("wpcide", "static")

# A ROM image is at most 512 KB; refuse anything wildly larger rather
# than reading an arbitrary amount into memory.
MAX_UPLOAD = 4 * 1024 * 1024



KNOWN_SOCKETS = {socket for entry in GAMES.values() for socket in entry["roms"]}


def _midi_options(query):
    """The compile choices, as the page sends them in the query string."""
    def one(name, default=""):
        return (query.get(name) or [default])[0]
    priority = [c for c in one("priority").split(",") if c.strip()]
    instruments = {}
    for pair in one("inst").split(","):
        part, _, ident = pair.partition(":")
        if part.strip() and ident.strip():
            instruments[int(part)] = int(ident)
    return {"block": one("block"), "priority": priority or None,
            "instruments": instruments, "loop": one("loop", "1") != "0",
            "keep_drums": one("drums", "0") == "1",
            "fit": one("fit", "cut") if one("fit", "cut") in ("cut", "thin") else "cut"}

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "wpcide"

    # -- plumbing ---------------------------------------------------

    def log_message(self, *args):
        pass                      # the console belongs to the user

    def _send(self, status, body, content_type="application/octet-stream",
              filename=None, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if filename:
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % filename)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            raise ValueError("el fichero es demasiado grande (%d bytes)" % length)
        return self.rfile.read(length)

    # -- routes -----------------------------------------------------

    def do_GET(self):
        route = urlparse(self.path)
        path = route.path
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/settings":
                return self._json(settings())
            if path == "/api/fetch":
                target = (parse_qs(route.query).get("url") or [""])[0]
                payload, kind = fetch(target)
                if not kind.startswith("image/"):
                    return self._json(
                        {"error": "eso no es una imagen (%s)" % kind}, 415)
                return self._send(200, payload, kind)
            if path == "/api/games":
                term = (parse_qs(route.query).get("q") or [""])[0]
                return self._json({"games": search(term),
                                   "ipdb_search": ipdb_search_url(term)})
            if path == "/api/progress":
                return self._json(self.ide.progress or {})
            if path == "/api/music":
                return self._json({"commands": self.ide.music_commands(),
                                   "capacity": self.ide.music_capacity()})
            if path == "/api/instruments":
                return self._json({"instruments": self.ide.instruments()})
            match = re.fullmatch(r"/api/instrument/(\d+)", path)
            if match:
                command = int((parse_qs(route.query).get("cmd") or ["14"])[0])
                return self._send(200, self.ide.instrument_wav(command, int(match.group(1))),
                                  "audio/wav")
            match = re.fullmatch(r"/api/midi/(\d+)/(fit|preview)", path)
            if match:
                options = _midi_options(parse_qs(route.query))
                command = int(match.group(1))
                if match.group(2) == "fit":
                    return self._json(self.ide.midi_fit(command, **options))
                data = self.ide.midi_preview(command, **options)
                if data is None:
                    return self._json({"error": "no sono nada"}, 404)
                return self._send(200, data, "audio/wav")
            match = re.fullmatch(r"/api/music/(\d+)/(fit|preview)", path)
            if match:
                query = parse_qs(route.query)
                command = int(match.group(1))
                blocks = [int(i) for i in
                          (query.get("blocks") or [""])[0].split(",") if i]
                offset = int((query.get("offset") or ["0"])[0])
                mode = (query.get("mode") or ["auto"])[0]
                chip = (query.get("chip") or ["dac"])[0]
                if match.group(2) == "fit":
                    return self._json(self.ide.music_fit(command, blocks, offset,
                                                         mode, chip))
                return self._send(200, self.ide.music_preview(
                    command, blocks, offset, mode, chip), "audio/wav")
            match = re.fullmatch(r"/api/music/(\d+)", path)
            if match:
                data = self.ide.music_wav(int(match.group(1)))
                if data is None:
                    return self._json({"error": "ese comando no hace musica"}, 404)
                return self._send(200, data, "audio/wav")
            if path == "/api/status":
                return self._json(self.ide.status())
            match = re.fullmatch(r"/api/dcs/wav/(\d+)", path)
            if match:
                original = bool(parse_qs(route.query).get("original"))
                return self._send(200, self.ide.dcs_wav(int(match.group(1)), original),
                                  "audio/wav")
            if path == "/api/map":
                return self._json(self.ide.view())
            match = re.fullmatch(r"/api/wav/(dac|cvsd)/(\d+)", path)
            if match:
                kind, index = match.group(1), int(match.group(2))
                query = parse_qs(route.query)
                data = self.ide.block_wav(kind, index,
                                          original=bool(query.get("original")))
                name = None
                if query.get("download"):
                    note = self.ide.note_for(kind, index)
                    name = self.ide._wav_name(self.ide.find(kind, index),
                                              note.get("note"))
                return self._send(200, data, "audio/wav", name)
            if path == "/api/bundle":
                marked = bool(parse_qs(route.query).get("marked"))
                data = self.ide.wav_bundle(only_marked=marked)
                return self._send(200, data, "application/zip",
                                  "sonidos%s.zip" % ("_marcados" if marked else ""))
            match = re.fullmatch(r"/api/preview/(dac|cvsd)/(\d+)", path)
            if match:
                query = parse_qs(route.query)
                offset = int((query.get("offset") or ["0"])[0])
                mode = (query.get("mode") or ["trim"])[0]
                data = self.ide.preview(match.group(1), int(match.group(2)),
                                        offset, mode)
                return self._send(200, data, "audio/wav")
            match = re.fullmatch(r"/api/fit/(dac|cvsd)/(\d+)", path)
            if match:
                query = parse_qs(route.query)
                return self._json(self.ide.fit_preview(
                    match.group(1), int(match.group(2)),
                    int((query.get("offset") or ["0"])[0]),
                    (query.get("mode") or ["trim"])[0]))
            if path == "/api/notes":
                payload = json.dumps(self.ide.export_notes(), ensure_ascii=False,
                                     indent=1).encode("utf-8")
                name = "notas_%s.json" % (self.ide.game.key if self.ide.game else "rom")
                return self._send(200, payload, "application/json; charset=utf-8", name)
            if path == "/api/download":
                name = "%s_editada.zip" % (self.ide.game.key if self.ide.game else "rom")
                return self._send(200, self.ide.patched(), "application/zip", name)
        except NotLoaded as problem:
            return self._json({"error": str(problem)}, 409)
        except FetchError as problem:
            return self._json({"error": str(problem)}, 502)
        except KeyError as problem:
            return self._json({"error": str(problem)}, 404)
        except ValueError as problem:
            return self._json({"error": str(problem)}, 400)
        except Exception as problem:                      # noqa: BLE001
            return self._json({"error": str(problem)}, 500)
        self._json({"error": "no existe %s" % path}, 404)

    def do_POST(self):
        route = urlparse(self.path)
        path = route.path
        query = parse_qs(route.query)
        try:
            if path == "/api/image":
                # Any socket some known game has: U18/U15/U14 on a pre-DCS
                # board, U2-U8 on a DCS one.
                slot = (query.get("socket") or [""])[0].lower()
                if slot not in KNOWN_SOCKETS:
                    return self._json({"error": "zocalo desconocido %r" % slot}, 400)
                self.ide.add_image(slot, self._body())
                return self._json(self.ide.status())
            if path == "/api/board":
                wanted = (query.get("platform") or [""])[0]
                self.ide.board = wanted if wanted in ("dcs", "wpcs") else None
                return self._json(self.ide.status())
            if path == "/api/romzip":
                self.ide.add_zip(self._body())
                return self._json(self.ide.status())
            match = re.fullmatch(r"/api/dcs/(\d+)/(preview|commit|revert)", path)
            if match:
                command = int(match.group(1))
                matching = (query.get("match") or ["1"])[0] != "0"
                if match.group(2) == "revert":
                    self.ide.dcs_revert(command)
                    return self._json({"ok": True})
                if match.group(2) == "commit":
                    return self._json(self.ide.dcs_commit(command, self._body(), matching))
                data, report = self.ide.dcs_preview(command, self._body(), matching)
                return self._send(200, data, "audio/wav",
                                  headers={"X-Report": json.dumps(report)})
            if path == "/api/load":
                game = self.ide.load()
                if game is None:
                    return self._json({
                        "game": None,
                        "sockets": sorted(self.ide.images),
                        "error": "estas imagenes no coinciden con ningun juego conocido",
                    })
                return self._json(self.ide.view())
            if path == "/api/notes":
                replace = bool(query.get("replace"))
                count = self.ide.import_notes(json.loads(self._body() or b"{}"),
                                              replace=replace)
                return self._json({"imported": count})
            if path == "/api/settings":
                return self._json(save_settings(json.loads(self._body() or b"{}")))
            if path == "/api/reset":
                self.server.ide = Ide()
                return self._json({"ok": True})
            match = re.fullmatch(r"/api/stage/(dac|cvsd)/(\d+)", path)
            if match:
                report = self.ide.stage(match.group(1), int(match.group(2)), self._body())
                return self._json(report)
            match = re.fullmatch(r"/api/commit/(dac|cvsd)/(\d+)", path)
            if match:
                offset = int((query.get("offset") or ["0"])[0])
                mode = (query.get("mode") or ["trim"])[0]
                return self._json(self.ide.commit(match.group(1),
                                                  int(match.group(2)), offset, mode))
            match = re.fullmatch(r"/api/replace/(dac|cvsd)/(\d+)", path)
            if match:
                report = self.ide.replace(match.group(1), int(match.group(2)), self._body())
                return self._json(report)
            match = re.fullmatch(r"/api/note/(dac|cvsd|music|dcs)/(\d+)", path)
            if match:
                payload = json.loads(self._body() or b"{}")
                return self._json(self.ide.annotate(
                    match.group(1), int(match.group(2)),
                    note=payload.get("note"), colour=payload.get("colour"),
                    category=payload.get("category"), marked=payload.get("marked")))
            match = re.fullmatch(r"/api/midi/(\d+)/(stage|commit)", path)
            if match:
                command = int(match.group(1))
                if match.group(2) == "stage":
                    return self._json(self.ide.midi_stage(command, self._body()))
                return self._json(self.ide.commit_midi(command, **_midi_options(query)))
            match = re.fullmatch(r"/api/music/(\d+)/(stage|commit|revert)", path)
            if match:
                command = int(match.group(1))
                blocks = [int(i) for i in
                          (query.get("blocks") or [""])[0].split(",") if i]
                offset = int((query.get("offset") or ["0"])[0])
                mode = (query.get("mode") or ["auto"])[0]
                chip = (query.get("chip") or ["dac"])[0]
                if match.group(2) == "stage":
                    return self._json(self.ide.stage_music(command, self._body(),
                                                           blocks or None, chip))
                if match.group(2) == "commit":
                    return self._json(self.ide.commit_music(command, blocks,
                                                            offset, mode, chip))
                self.ide.revert_music(command)
                return self._json({"ok": True})
            match = re.fullmatch(r"/api/revert/(dac|cvsd)/(\d+)", path)
            if match:
                self.ide.revert(match.group(1), int(match.group(2)))
                return self._json({"ok": True})
        except NotLoaded as problem:
            return self._json({"error": str(problem)}, 409)
        except KeyError as problem:
            return self._json({"error": str(problem)}, 404)
        except Exception as problem:                      # noqa: BLE001
            return self._json({"error": str(problem)}, 400)
        self._json({"error": "no existe %s" % path}, 404)

    # -- helpers ----------------------------------------------------

    @property
    def ide(self):
        return self.server.ide

    def _static(self, name):
        target = (STATIC / name).resolve()
        if not str(target).startswith(str(STATIC)) or not target.is_file():
            return self._json({"error": "no existe %s" % name}, 404)
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind.endswith("javascript"):
            kind += "; charset=utf-8"
        self._send(200, target.read_bytes(), kind)


class EditorServer(ThreadingHTTPServer):
    """The HTTP server, holding its port to itself.

    `HTTPServer` sets SO_REUSEADDR, and on Windows that lets a second
    program listen on a port that is already taken: two editors would
    both "start", and requests would go to either. Exclusive use makes a
    taken port fail loudly instead, which `serve` can then deal with.
    """

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _editor_at(port):
    """True if an editor is already answering on this port."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port, timeout=1.5) as reply:
            return "sockets" in json.loads(reply.read().decode("utf-8"))
    except (OSError, ValueError):
        return False


def serve(port=8765, open_browser=True, tries=10):
    """Runs the editor until interrupted.

    A taken port is the usual reason an editor "does nothing": the
    window flashes and closes. So if an editor is already running there,
    this one just opens the browser at it; if something else holds the
    port, the next free one is used.
    """
    server = None
    for candidate in range(port, port + tries):
        try:
            server = EditorServer(("127.0.0.1", candidate), Handler)
            break
        except OSError:
            if _editor_at(candidate):
                url = "http://127.0.0.1:%d/" % candidate
                print("Ya hay un editor abierto en %s; lo abro en el navegador." % url, flush=True)
                if open_browser:
                    webbrowser.open(url)
                return None
            print("El puerto %d esta ocupado por otro programa; pruebo el siguiente." % candidate,
                  flush=True)
    if server is None:
        raise OSError("no hay ningun puerto libre entre %d y %d" % (port, port + tries - 1))

    server.ide = Ide()
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    print("Editor de sonido WPC en %s" % url, flush=True)
    print("Deja esta ventana abierta mientras lo uses. Ctrl+C para salir.", flush=True)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrado.")
    finally:
        server.server_close()
    return server
