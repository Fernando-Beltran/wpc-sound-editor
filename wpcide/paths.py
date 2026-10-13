"""Where things live, running from source and running packaged.

Two kinds of files, and conflating them is what breaks a packaged app:

- **Bundled**: the page, the fonts, the sound maps, VGMPlay. They ship
  with the program and are read-only. Running from source they sit in
  the project; frozen by PyInstaller they sit wherever it unpacked the
  bundle, which `sys._MEIPASS` names.
- **The user's**: annotations, the render cache, network settings.
  These are written, and they must not land in whatever directory the
  program happened to be started from -- which for a packaged app is
  the desktop, or `System32`, or anywhere at all.

So the user's files go to `%LOCALAPPDATA%\\EditorWPC` on Windows and
`~/.local/share/editorwpc` elsewhere, and `WPCIDE_DATA` overrides that
for anyone who wants it somewhere else.
"""

import os
import pathlib
import sys

APP = "EditorWPC"
DATA_ENV = "WPCIDE_DATA"


def frozen():
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def root():
    """The directory bundled files are read from."""
    if frozen():
        return pathlib.Path(sys._MEIPASS)      # noqa: SLF001 -- PyInstaller's own
    return pathlib.Path(__file__).resolve().parent.parent


def bundled(*parts):
    """A path to something that ships with the program."""
    return root().joinpath(*parts)


def user_data(*parts):
    """A path under the user's own data directory, created on demand."""
    override = os.environ.get(DATA_ENV)
    if override:
        base = pathlib.Path(override)
    elif sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA") or (pathlib.Path.home() / "AppData" / "Local")
        base = pathlib.Path(local) / APP
    elif sys.platform == "darwin":
        base = pathlib.Path.home() / "Library" / "Application Support" / APP
    else:
        base = pathlib.Path(os.environ.get("XDG_DATA_HOME",
                                           pathlib.Path.home() / ".local" / "share"))
        base = base / APP.lower()
    target = base.joinpath(*parts)
    (target if not parts else target.parent).mkdir(parents=True, exist_ok=True)
    return target


def writable_copy(*parts):
    """A writable copy of something bundled, made once.

    VGMPlay needs this: it rewrites its own `.ini` to switch between
    playing and logging, and a bundle directory is the wrong place to
    write -- read-only in the good case, deleted on exit in the
    one-file case.
    """
    import shutil

    source = bundled(*parts)
    target = user_data(*parts)
    if target.exists():
        return target
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    elif source.exists():
        shutil.copy2(source, target)
    return target
