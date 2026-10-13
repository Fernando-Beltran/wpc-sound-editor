# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the WPC sound editor.

Built as a **folder**, not a single file, on purpose: VGMPlay rewrites
its own `VGMPlay.ini` to switch between playing and logging, and a
one-file build unpacks to a temporary directory that is read-only in
spirit and deleted on exit. A folder build also starts faster, which
matters for something the user opens to listen to a sound.

    pip install pyinstaller
    pyinstaller EditorWPC.spec

Everything the program writes -- annotations, the render cache, network
settings -- goes to the user's own data directory, never beside the
executable. See `wpcide/paths.py`.
"""

datos = [
    ("wpcide/static", "wpcide/static"),   # the page, its fonts, its icons
    ("tools/vgmplay", "tools/vgmplay"),   # the YM2151 renderer
    # DCS Explorer and DCS Encoder (BSD-3, Michael J Roberts): decode and
    # rebuild DCS sound ROMs. Their LICENSE travels with them, as the
    # licence requires. They only read from here and write into the
    # work folder they are given, which is in the user's data directory,
    # so unlike VGMPlay they need no writable copy.
    ("tools/dcsexplorer/DCSExplorer.exe", "tools/dcsexplorer"),
    ("tools/dcsexplorer/DCSEncoder.exe", "tools/dcsexplorer"),
    ("tools/dcsexplorer/LICENSE", "tools/dcsexplorer"),
    ("tools/dcsexplorer/README.md", "tools/dcsexplorer"),
    ("mapas", "mapas"),                   # which command plays which sound
]

analisis = Analysis(
    ["wpcide/__main__.py"],
    pathex=["."],
    datas=datos,
    hiddenimports=["wpcaudio", "wpc6809"],
    # Nothing here draws a window of its own or talks to a database.
    excludes=["tkinter", "matplotlib", "scipy", "PIL", "pytest", "sqlite3"],
    noarchive=False,
)

pyz = PYZ(analisis.pure)

exe = EXE(
    pyz,
    analisis.scripts,
    exclude_binaries=True,
    name="EditorWPC",
    console=True,          # it prints the address it is serving on
    upx=False,
)

coll = COLLECT(
    exe,
    analisis.binaries,
    analisis.datas,
    upx=False,
    name="EditorWPC",
)
