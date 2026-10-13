# External tools / Herramientas externas

These programs are not part of this repository: each keeps its own licence and you
download it yourself. The editor finds them in these folders.

Estos programas no forman parte del repositorio: cada uno tiene su licencia y los
descargas tú. El editor los busca en estas carpetas.

| Folder / Carpeta | Program / Programa | Needed for / Para qué | Get it / Dónde |
| --- | --- | --- | --- |
| `tools/vgmplay/` | **VGMPlay** 0.40.x (`VGMPlay.exe`, `VGMPlay.ini`, `zlib1.dll`) | Hearing the FM music of pre-DCS games / Oír la música FM de los juegos anteriores a DCS | github.com/vgmrips/vgmplay-legacy (releases) |
| `tools/dcsexplorer/` | **DCS Explorer** and **DCS Encoder** v1.1 (`DCSExplorer.exe`, `DCSEncoder.exe`) | Everything DCS / Todo lo de DCS | github.com/mjrgh/DCSExplorer (releases) |
| `tools/pinmame/` | **PinMAME** 3.6 (`PinMAME.exe`) | Testing patched sets / Probar las ROM parcheadas | github.com/vpinball/pinmame (releases) |

`tools/dcsexplorer/LICENSE` and `README.md` are DCS Explorer's own licence and readme, kept
here because the BSD licence asks for them to travel with the binaries.

`tools/dcsexplorer/LICENSE` y `README.md` son la licencia y el léame de DCS Explorer; están
aquí porque su licencia BSD pide que acompañen a los ejecutables.

The Windows package (`EditorWPC.spec`) bundles VGMPlay and DCS Explorer from these
folders, so they must be in place before building it. VGMPlay goes in with its own
`licenses/` folder (old MAME licence, GPL and others) and a `SOURCE.txt` pointing to its
source code; because of the MAME licence, the package must not be sold.

El paquete de Windows (`EditorWPC.spec`) incluye VGMPlay y DCS Explorer desde estas
carpetas, así que tienen que estar antes de construirlo. VGMPlay va con su carpeta
`licenses/` (licencia antigua de MAME, GPL y otras) y un `SOURCE.txt` que indica dónde está
su código fuente; por la licencia de MAME, el paquete no se puede vender.
