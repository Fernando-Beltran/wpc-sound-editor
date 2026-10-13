# User manual — WPC Sound Editor

*[Versión en castellano](manual.es.md)*

This editor opens the sound ROMs of a Williams / Bally WPC machine, lets you hear and
replace its sounds, and gives you back patched images ready for PinMAME or for burning
into EPROMs. Everything happens on your computer: the browser page talks to a small local
program and nothing is uploaded anywhere.

---

## 1. Starting the editor

- **From source:** `python -m wpcide`. Your browser opens at `http://127.0.0.1:8765/`.
- **Packaged:** open **`dist\EditorWPC\EditorWPC.exe`**. A black window shows the editor's
  address, then your browser opens. **Keep that window open** while you work: closing it
  stops the editor.

If an editor is already running, launching another just opens your browser on the first.
If something stops it from starting, the window shows the error and waits for Enter.

The language button (flag) at the top right switches between Spanish and English.

---

## 2. Loading the ROMs

1. Choose the board type: **Pre-DCS (1990-1993)** — three sockets, U18, U15 and U14 — or
   **DCS (1993-1998)** — seven sockets, U2 to U8.
2. Drop the files on the box, in any of these ways:
   - **PinMAME's whole romset zip** (each file finds its socket by its CRC);
   - **the whole extracted folder**: the readme, the licence PDF and so on are skipped;
   - **one file on each socket**, if its name does not make the socket clear.
3. Each socket turns green when the image matches a known game. Once all are in, the
   editor identifies the machine and opens its view.

Known games so far: *Twilight Zone* (sound L-1 and L-2) and *Star Trek: The Next
Generation* (sound L-1 and special LX-7). An image that does not match is marked red: the
editor's map was built on those exact dumps and would not apply to others.

**Finding the machine:** the finder on the load screen opens the game's IPDB page, where
the official images are published with Planetary Pinball Supply's permission. The editor
never downloads anything itself: you fetch the file in your browser and drop it here.

---

## 3. Pre-DCS games (Twilight Zone)

### 3.1 The chip map

Each ROM is drawn as its chip, with its 32 KB pages and every sound block at its true
position. Blue is voice (HC55516 / CVSD), orange DAC samples, green what you have edited;
each block wears its own loudness silhouette.

Click a block to see its details (chip, position, size, length, which commands trigger it)
and to hear it.

### 3.2 Replacing a block with a WAV

1. With the block chosen, **Replace** and pick your WAV.
2. The report compares your audio's length with what the block holds. **A block cannot
   change size**, so you choose how to make it fit:
   - **Trim** — keeps a block-sized window; the slider picks which.
   - **Loop** — repeats the audio until the block is full.
   - **Speed** — stretches or squeezes it to fit exactly (changes the pitch).
   - **Automatic** — nudges the speed for a near miss, otherwise trims or loops.
3. **Hear how it would sound** plays the audio already encoded and decoded: what the
   machine will really play, codec losses included.
4. **Apply**. **Original** lets you compare at any time; **Restore** undoes it.

### 3.3 Annotating

Every block takes a note, a category (voice, effect, music, ambience, jingle…), a colour
of its own and a "to edit" mark. Notes are kept with your data and can be exported and
imported as JSON.

### 3.4 The music (Music tab)

This board's music is not recorded: it is notes the program sends to the YM2151 FM chip.
The editor **emulates the board** to hear it.

- **Listen** renders a command's music (a few seconds the first time; kept afterwards).
  **Render them all** prepares every one, with a progress bar.
- Music takes the same annotations as a block.

**Replacing a music cue with a WAV.** The FM chip cannot play audio, so the command is
made to play your recording on a sample chip:
- **DAC** — high quality (8-bit at 11 kHz), very little free space;
- **CVSD** — voice quality, about six times the space.

The audio takes over existing blocks: the editor offers unused ones first and tells you
what each one stops playing.

**Replacing a music cue with a MIDI file.** The MIDI is compiled into the game's own FM
music:
1. Load the `.mid`. You see the original song's **parts**, each with its instrument.
2. For each part you can choose **one of the game's 104 instruments** and hear it with ▶.
3. Order the **MIDI channels by priority**: the first never lose notes; the rest fill the
   gaps (the chip has 8 channels and some parts use two voices).
4. Choose the **borrowed block** the notes go into. If it does not fit, decide whether to
   **cut the end** or **drop the lowest-priority channels**. Spare room needs no filling:
   the song loops by itself.
5. **Hear how it would sound** (about 30 s of rendering) and **Apply**.

Some commands share programs: if you change one, the report tells you which others change
too.

### 3.5 Downloading

**Download ROM** saves a zip with the three patched images, the same size as the originals
and with the **power-on checksum fixed** (without that fix the board reports an error at
power-on), plus a README listing every change.

---

## 4. DCS games (Star Trek: The Next Generation)

### 4.1 The track list

Each track is what plays when the machine asks for it. Filter by channel — **0 is the
music**, 1, 2 and 3 voices and effects — and search by command (`01 76`) or by your note.
The ⟳ icon marks a looping track.

On the right are **the seven chips** with every piece of audio at its real place. Picking
a track lights the pieces it plays in orange; clicking a piece picks a track that uses it.

### 4.2 Replacing a track with your own audio

1. Pick the track and press **Replace with audio** (WAV, MP3 or anything your browser
   can read).
2. Your audio is drawn full width, with a **window the size of the slot** (the original
   track's length) placed where the sound starts.
   - **Drag the window** along the waveform: let go and you hear what is inside. With
     **Keep playing while I move it** it loops and follows you.
   - **Pull its edges** to widen or narrow it, or type the start and end.
   - **Keep the slot's size** makes the edges move the whole window.
3. **Fit to the slot**, when your selection is not the track's length:
   - **Do not fit** — the new track lasts as long as your selection;
   - **Speed up or slow down, keeping the pitch** — the voice does not turn squeaky;
   - **Speed up or slow down, changing the pitch** — like a faster tape.
   The selection already plays at its final speed when you listen to it.
4. **Match the original track's loudness** is on by default.
5. **Hear it in the ROM** (about 12 s) encodes your audio to DCS and plays what the machine
   will play. **Apply** commits it; **Restore the original track** undoes it.

The track is **re-programmed** around your audio: it keeps its volume and how it ducks the
music while it plays, and audio it shares with other tracks is left alone.

**Space.** The set must stay at seven chips to fit the board. The editor tries the best
quality first and uses the highest that fits; if even the lowest does not, it says so.
Replacing a track frees the audio only that track used.

### 4.3 Downloading

**Download ROM** saves the seven chips under the PinMAME romset's names, with a README.

---

## 5. Testing before burning

1. **PinMAME**: put the downloaded images into the romset zip (for example `sttng_l7.zip`,
   next to the CPU ROM) and run it. It warns that the CRCs do not match — normal for a
   modified ROM — and continues on a key press.
2. To hear a particular sound, the machine's service menu has a **sound test** (in PinMAME
   the coin-door keys are 7 escape, 8 minus, 9 plus, 0 enter). It offers a selection of
   sounds only.
3. **On the real machine**: burn the images into EPROMs of the same type (27C010 / 27C040)
   with a programmer (TL866, T48…). Do it at your own risk, and always keep the originals.

---

## 6. Troubleshooting

| What happens | What to do |
| --- | --- |
| I open the exe and nothing happens | Open `dist\EditorWPC\EditorWPC.exe`, not the one in `build\`. |
| "That command produced no music" | Read the message: if it says there is no connection, the editor closed; start it and load the ROMs again. |
| A socket turns red | That image is not from a known game (another revision, or a damaged or empty file). |
| No FM music | VGMPlay is missing from `tools/vgmplay/` (see `tools/README.md`). |
| A DCS game will not open | DCS Explorer is missing from `tools/dcsexplorer/`. |
| "It does not fit" | Use less audio, lower quality (DCS does it for you) or a bigger block. |

Your data (notes, rendered music, settings) lives in `%LOCALAPPDATA%\EditorWPC`.
