"use strict";

const $ = (id) => document.getElementById(id);
const PAGE = 0x8000;
const COLOUR = { dac: "#e2a03f", cvsd: "#4aa3df" };

let state = null;          // the chip map, once a game is identified
let chosen = null;         // the block being looked at
let chosenMusic = null;    // the music command being looked at
let musicRows = [];
let musicLoaded = false;

// ====================================================================
//  Loading the ROM images
// ====================================================================

// U18/U15/U14 on a pre-DCS board, U2-U9 on a DCS one ("ng_u2_s.l1").
const SOCKET = /u(1[458]|[2-9])(?![0-9])/i;

function socketOf(name) {
  const match = SOCKET.exec(name);
  return match ? "u" + match[1].toLowerCase() : null;
}

function working(on, key) {
  $("dropzone").classList.toggle("working", on);
  $("dropbusy").hidden = !on;
  if (on && key) $("dropbusy-text").textContent = t(key);
}

async function offer(files, forced) {
  $("dropmsg").textContent = "";
  working(true, "drop.reading");
  let taken = 0;
  for (const file of files) {
    // A whole romset zip sorts itself out by CRC on the server.
    if (/\.zip$/i.test(file.name)) {
      const response = await fetch("/api/romzip", { method: "POST", body: await file.arrayBuffer() });
      const body = await response.json();
      if (body.error) { $("dropmsg").textContent = body.error; continue; }
      taken += 1;
      drawSlots(body);
      continue;
    }
    // An EPROM image is a power of two, 16 KB or more. The readme, the
    // licence PDF and the rest of what comes in a ROM folder are not,
    // so a whole folder can be dropped without complaints about them.
    const size = file.size;
    if (!forced && (size < 16384 || (size & (size - 1)) !== 0)) continue;
    // Dropped on a socket box, that box wins: the file name is a guess
    // and the user pointing at the socket is not.
    const socket = forced || socketOf(file.name);
    if (!socket) {
      $("dropmsg").textContent = t("drop.unknownSocket", { name: file.name });
      continue;
    }
    const response = await fetch(`/api/image?socket=${socket}`, {
      method: "POST", body: await file.arrayBuffer(),
    });
    const body = await response.json();
    if (body.error) { $("dropmsg").textContent = body.error; continue; }
    taken += 1;
    drawSlots(body);
  }
  if (taken) {
    await identify();
  }
  working(false);
}

function drawBoard(platform) {
  for (const button of $("board-pick").querySelectorAll("button")) {
    button.classList.toggle("on", button.dataset.board === platform);
  }
}

function drawSlots(status) {
  drawBoard(status.platform);
  $("slots").innerHTML = status.sockets.map((slot) => {
    const label = slot.loaded
      ? (slot.ok === false ? t("slot.mismatch")
         : t("slot.size", { kb: Math.round(slot.size / 1024) }) +
           (slot.ok ? " · " + t("slot.ok") : ""))
      : t("slot.drop");
    const icon = slot.loaded
      ? (slot.ok === false ? "fa-triangle-exclamation" : "fa-circle-check")
      : "fa-inbox";
    const classes = ["slot", slot.loaded ? "filled" : "",
                     slot.ok === false ? "bad" : ""].join(" ");
    return `<label class="${classes}" data-socket="${slot.socket}">
        <input type="file" accept=".zip,.rom,.bin,.u14,.u15,.u18,.l1">
        <b><i class="fa-solid ${icon}"></i> ${slot.socket.toUpperCase()}</b>
        <span class="state">${label}</span>
        <span class="crc">${slot.crc ? "CRC " + slot.crc : "&nbsp;"}</span>
      </label>`;
  }).join("");

  for (const label of document.querySelectorAll(".slot")) {
    const socket = label.dataset.socket;
    label.querySelector("input").addEventListener("change", (event) => {
      if (event.target.files[0]) offer([event.target.files[0]], socket);
    });
    for (const name of ["dragenter", "dragover"]) {
      label.addEventListener(name, (event) => {
        event.preventDefault(); event.stopPropagation();
        label.classList.add("over");
      });
    }
    label.addEventListener("dragleave", () => label.classList.remove("over"));
    label.addEventListener("drop", (event) => {
      event.preventDefault(); event.stopPropagation();
      label.classList.remove("over");
      if (event.dataTransfer.files[0]) offer([event.dataTransfer.files[0]], socket);
    });
  }
}

async function identify() {
  working(true, "drop.identifying");
  const body = await (await fetch("/api/load", { method: "POST" })).json();
  if (body.error && !body.game) {
    const have = (body.sockets || []).length;
    // Only once every socket shown is filled: three on a pre-DCS board,
    // seven on STTNG.
    $("dropmsg").textContent =
      have >= document.querySelectorAll(".slot").length ? body.error : "";
    return;
  }
  state = body;
  render();
}

// ====================================================================
//  One player, one spectrum, one playhead
// ====================================================================

let analyser = null;
let drawingSpectrum = false;
let currentWave = null;     // peaks of whatever is loaded
let waveTarget = null;      // which canvas shows it

function listen() {
  const player = $("player");
  if (analyser) return player;
  // A Web Audio source can only be attached to an element once, so one
  // element serves every sound on the page.
  const context = new (window.AudioContext || window.webkitAudioContext)();
  const source = context.createMediaElementSource(player);
  analyser = context.createAnalyser();
  analyser.fftSize = 512;
  analyser.smoothingTimeConstant = 0.78;
  source.connect(analyser);
  analyser.connect(context.destination);
  player.addEventListener("play", () => { context.resume(); spectrum(); follow(); });
  player.addEventListener("ended", () => drawWave());
  return player;
}

async function sound(url, canvas) {
  const player = listen();
  waveTarget = canvas || null;
  currentWave = null;
  drawWave();
  player.src = url;
  player.play().catch(() => {});
  // The waveform is drawn from the audio itself rather than the
  // server's thumbnail, so the playhead has something exact to run over.
  try {
    const data = await (await fetch(url)).arrayBuffer();
    const context = new (window.AudioContext || window.webkitAudioContext)();
    const buffer = await context.decodeAudioData(data);
    currentWave = peaks(buffer, 600);
    context.close();
    drawWave();
  } catch (ignored) { /* a format the browser will not decode */ }
}

function stopSound() {
  const player = $("player");
  player.pause();
  player.currentTime = 0;
  drawWave();
}

function peaks(buffer, count) {
  const channel = buffer.getChannelData(0);
  const per = Math.max(1, Math.floor(channel.length / count));
  const out = new Float32Array(count);
  let loudest = 0;
  for (let i = 0; i < count; i++) {
    let top = 0;
    for (let j = i * per; j < (i + 1) * per && j < channel.length; j++) {
      const value = Math.abs(channel[j]);
      if (value > top) top = value;
    }
    out[i] = top;
    if (top > loudest) loudest = top;
  }
  if (!loudest) return out;
  for (let i = 0; i < count; i++) out[i] /= loudest;
  return out;
}

function drawWave() {
  const canvas = waveTarget;
  if (!canvas) return;
  const pen = canvas.getContext("2d");
  const { width, height } = canvas;
  pen.clearRect(0, 0, width, height);

  if (!currentWave) {
    pen.fillStyle = "#2a2e36";
    pen.fillRect(0, height / 2 - 1, width, 2);
    return;
  }

  const middle = height / 2;
  pen.fillStyle = canvas.id === "music-wave" ? "#b07fd4" : "#4aa3df";
  const step = width / currentWave.length;
  for (let i = 0; i < currentWave.length; i++) {
    const h = Math.max(1, currentWave[i] * (middle - 3));
    pen.fillRect(i * step, middle - h, Math.max(1, step), h * 2);
  }

  const player = $("player");
  if (player.duration) {
    const x = (player.currentTime / player.duration) * width;
    pen.fillStyle = "#ffffff";
    pen.fillRect(x - 1, 0, 2, height);
  }
}

function follow() {
  const frame = () => {
    drawWave();
    if (!$("player").paused) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

function spectrum() {
  if (drawingSpectrum) return;
  drawingSpectrum = true;
  const canvas = $("spectrum");
  const pen = canvas.getContext("2d");
  const bins = new Uint8Array(analyser.frequencyBinCount);

  const frame = () => {
    analyser.getByteFrequencyData(bins);
    pen.clearRect(0, 0, canvas.width, canvas.height);
    // Only the lower bins carry anything at these rates.
    const shown = Math.floor(bins.length * 0.55);
    const width = canvas.width / shown;
    for (let i = 0; i < shown; i++) {
      const height = (bins[i] / 255) * canvas.height;
      pen.fillStyle = `hsl(${205 - (i / shown) * 60} 70% ${28 + (bins[i] / 255) * 42}%)`;
      pen.fillRect(i * width, canvas.height - height, width + 0.6, height);
    }
    if (!$("player").paused) {
      requestAnimationFrame(frame);
    } else {
      pen.clearRect(0, 0, canvas.width, canvas.height);
      drawingSpectrum = false;
    }
  };
  requestAnimationFrame(frame);
}

function seekFrom(canvas) {
  canvas.addEventListener("click", (event) => {
    const player = $("player");
    if (!player.duration) return;
    const box = canvas.getBoundingClientRect();
    player.currentTime = ((event.clientX - box.left) / box.width) * player.duration;
    drawWave();
  });
}

// ====================================================================
//  The chip map
// ====================================================================

function human(bytes) {
  return bytes >= 1024 ? `${(bytes / 1024).toFixed(0)} KB` : `${bytes} B`;
}

function silhouette(shape, colour) {
  const n = shape.length;
  const top = [], bottom = [];
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 100;
    const h = (shape[i] / 15) * 46;
    top.push(`${x.toFixed(2)},${(50 - h).toFixed(2)}`);
    bottom.unshift(`${x.toFixed(2)},${(50 + h).toFixed(2)}`);
  }
  const svg =
    `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100' ` +
    `preserveAspectRatio='none'><polygon points='${top.concat(bottom).join(" ")}' ` +
    `fill='${colour}'/><line x1='0' y1='50' x2='100' y2='50' ` +
    `stroke='${colour}' stroke-width='1.5'/></svg>`;
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
}

function render() {
  $("drop").hidden = true;
  if (state.platform === "dcs") {
    for (const id of ["download", "restart", "notes-export"]) $(id).hidden = false;
    $("bundle").hidden = true;
    $("bundle-marked").hidden = true;
    $("notes-import-wrap").hidden = false;
    $("game").textContent = state.game.name;
    $("flyer").hidden = false;
    $("flyer-ipdb").href = state.game.flyers;
    restoreFlyer();
    renderDcs();
    return;
  }
  $("dcs").hidden = true;
  $("tabs").hidden = false;
  for (const id of ["download", "restart", "bundle", "notes-export"]) $(id).hidden = false;
  $("notes-import-wrap").hidden = false;
  $("bundle-marked").hidden = state.marked.length === 0;
  $("bundle-marked-text").textContent =
    t("actions.bundleMarked", { n: state.marked.length });
  $("game").textContent = state.game.name;
  $("flyer").hidden = false;
  $("flyer-ipdb").href = state.game.flyers;
  restoreFlyer();

  const legend = `<p class="legend">
      <span><i style="background:var(--cvsd)"></i>${t("legend.cvsd")}</span>
      <span><i style="background:var(--dac)"></i>${t("legend.dac")}</span>
      <span><i style="background:var(--edited)"></i>${t("legend.edited")}</span>
      <span><i style="background:#23262d"></i>${t("legend.program")}</span>
    </p>`;
  $("chips").innerHTML = legend + state.chips.map(chipHtml).join("");

  for (const button of document.querySelectorAll(".block")) {
    const block = blockOf(button.dataset.kind, Number(button.dataset.index));
    // Assigned as a property: a data URI carries quotes, and inside a
    // style="..." attribute the first one would end the attribute.
    button.style.backgroundImage = silhouette(block.shape, block.edited
      ? "#58c26a" : (block.colour || COLOUR[block.kind]));
    button.addEventListener("click", () => select(button.dataset.kind,
                                                  Number(button.dataset.index)));
  }
  if (chosen) select(chosen.kind, chosen.index, true);
  mirror();

  // Reveal a view if none is showing yet. Checked rather than forced,
  // so re-rendering after an edit does not drag you off the music tab.
  if ($("workspace").hidden && $("music").hidden) showTab("map");
}

function chipHtml(chip) {
  const byPage = new Map();
  for (const block of chip.blocks) {
    const page = Math.floor(block.offset / PAGE);
    if (!byPage.has(page)) byPage.set(page, []);
    byPage.get(page).push(block);
  }

  const percent = (chip.covered / chip.size) * 100;
  const rows = [];
  for (let page = 0; page < chip.pages; page++) {
    const blocks = (byPage.get(page) || []).map((block) => {
      const left = ((block.offset % PAGE) / PAGE) * 100;
      const width = (block.size / PAGE) * 100;
      const classes = ["block", block.kind, block.edited ? "edited" : "",
                       block.marked ? "marked" : ""].join(" ");
      return `<button class="${classes}" style="left:${left}%;width:${width}%"
              data-kind="${block.kind}" data-index="${block.index}"
              title="${block.note || block.kind + ":" + block.index} — ${
                human(block.size)}, ${block.seconds.toFixed(2)} s"></button>`;
    }).join("");
    rows.push(`<div class="page"><span class="label">${
      (page * PAGE).toString(16).toUpperCase().padStart(6, "0")
    }</span>${blocks}</div>`);
  }

  const pins = `<div class="pins">${"<i></i>".repeat(Math.max(8, chip.pages))}</div>`;
  const part = chip.size >= 0x80000 ? "27C040" : "27C010";
  return `<div class="chip">
      ${pins.replace('class="pins"', 'class="pins left"')}
      <div class="body">
        <div class="notch"></div>
        <div class="silk"><b>${chip.name.toUpperCase()}</b> · ${part} ·
          ${human(chip.size)} · ${t("chip.meta", {
            sounds: chip.blocks.length, pct: percent.toFixed(1) })}</div>
        <div class="die">${rows.join("")}</div>
      </div>
      ${pins.replace('class="pins"', 'class="pins right"')}
    </div>`;
}

function blockOf(kind, index) {
  for (const chip of state.chips) {
    for (const block of chip.blocks) {
      if (block.kind === kind && block.index === index) return block;
    }
  }
  return null;
}

// ====================================================================
//  One block
// ====================================================================

/** Categories carry their own colour, so they are shown wearing it. */
function categoryChips(container, current, onPick) {
  container.innerHTML = Object.entries(state.categories).map(([name, colour]) =>
    `<button class="chip-cat ${name === current ? "on" : ""}" data-cat="${name}"
       style="--c:${colour}">${t("cat." + name)}</button>`).join("") +
    `<button class="chip-cat ${!current ? "on" : ""}" data-cat="">${t("cat.")}</button>`;
  for (const button of container.querySelectorAll(".chip-cat")) {
    button.addEventListener("click", () => onPick(button.dataset.cat));
  }
}

function select(kind, index, keepMessage) {
  chosen = blockOf(kind, index);
  if (!chosen) return;

  for (const button of document.querySelectorAll(".block.sel")) {
    button.classList.remove("sel");
  }
  const button = document.querySelector(
    `.block[data-kind="${kind}"][data-index="${index}"]`);
  if (button) button.classList.add("sel");

  $("hint").hidden = true;
  $("chosen").hidden = false;
  $("chosen-title").textContent = `${kind.toUpperCase()} ${index}`;

  const facts = [
    ["fact.chip", chosen.chip.toUpperCase()],
    ["fact.offset", "0x" + chosen.offset.toString(16).toUpperCase().padStart(6, "0")],
    ["fact.size", t("fact.bytes", { n: chosen.size.toLocaleString() })],
    ["fact.seconds", `${chosen.seconds.toFixed(2)} s`],
    ["fact.bank", "$" + chosen.bank.toString(16).toUpperCase()],
    ["fact.range", `$${chosen.start.toString(16).toUpperCase()}–$${
      chosen.end.toString(16).toUpperCase()}`],
  ];
  if (chosen.commands && chosen.commands.length) {
    facts.push(["fact.commands", chosen.commands
      .map((c) => "$" + c.toString(16).toUpperCase().padStart(2, "0")).join(", ")]);
  }
  $("chosen-facts").innerHTML = facts
    .map(([key, value]) => `<dt>${t(key)}</dt><dd>${value}</dd>`).join("");

  $("save-wav").href = `/api/wav/${kind}/${index}?download=1`;
  $("marked").checked = chosen.marked;
  $("note").value = chosen.note || "";
  $("colour").value = chosen.colour || COLOUR[kind];
  $("compare").hidden = !chosen.edited;
  categoryChips($("category"), chosen.category || "",
                (cat) => annotate({ category: cat }));
  if (!keepMessage) { unstage(); $("fit-done").textContent = ""; }
  play();
}

function play(original) {
  if (!chosen) return;
  sound(`/api/wav/${chosen.kind}/${chosen.index}?t=${Date.now()}${
    original ? "&original=1" : ""}`, $("wave"));
}

// -- replacing it ------------------------------------------------------

let staged = null;
let mode = "auto";

function seconds(value) { return `${value.toFixed(2)} s`; }

async function stage(file) {
  const response = await fetch(`/api/stage/${chosen.kind}/${chosen.index}`, {
    method: "POST", body: await file.arrayBuffer(),
  });
  const body = await response.json();
  if (body.error) {
    $("fit").className = "fit warn";
    $("fit").textContent = body.error;
    $("trim").hidden = false;
    return;
  }
  staged = body;
  mode = "auto";
  $("trim").hidden = false;
  $("fit-done").textContent = "";
  $("offset").value = 0;
  drawModes();
  await refit();
  hear();
}

function drawModes() {
  $("modes").innerHTML = ["auto", "trim", "loop", "speed"].map((name) =>
    `<button data-mode="${name}" class="${name === mode ? "on" : ""}"
       title="${t("mode." + name + "Hint")}">${t("mode." + name)}</button>`).join("");
  $("mode-hint").textContent = t("mode." + mode + "Hint");
  for (const button of $("modes").querySelectorAll("button")) {
    button.addEventListener("click", async () => {
      mode = button.dataset.mode;
      drawModes();
      await refit();
      hear();
    });
  }
}

async function refit() {
  const offset = Number($("offset").value);
  const body = await (await fetch(
    `/api/fit/${chosen.kind}/${chosen.index}?offset=${offset}&mode=${mode}`)).json();
  staged = Object.assign({}, staged, body);
  $("offset").max = staged.max_offset;
  $("offset-wrap").hidden = staged.mode !== "trim" || staged.max_offset === 0;
  describe(Number($("offset").value));
}

function describe(offset) {
  const at = offset / staged.rate;
  const amounts = { source: seconds(staged.source_seconds),
                    capacity: seconds(staged.capacity_seconds) };
  const bits = [t(staged.longer ? "trim.longer" : "trim.shorter", amounts)];
  if (staged.mode === "speed") {
    bits.push(t("mode.speedInfo", { pct: (100 * staged.speed).toFixed(0) }));
  }
  $("fit").className = staged.truncated ? "fit warn" : "fit";
  $("fit").textContent = bits.join(" ");
  $("trim-label").textContent = staged.max_offset === 0
    ? t("trim.exact")
    : t(staged.longer ? "trim.startAt" : "trim.silenceBefore", { at: seconds(at) });
}

function hear() {
  if (!staged) return;
  sound(`/api/preview/${chosen.kind}/${chosen.index}?offset=${
    Number($("offset").value)}&mode=${mode}&t=${Date.now()}`, $("wave"));
}

function unstage() {
  staged = null;
  $("trim").hidden = true;
  $("fit").textContent = "";
}

async function apply() {
  const body = await (await fetch(
    `/api/commit/${chosen.kind}/${chosen.index}?offset=${
      Number($("offset").value)}&mode=${mode}`, { method: "POST" })).json();
  const bits = [t("trim.applied")];
  if (body.truncated) {
    bits.push(t("trim.truncated", { amount: seconds(body.truncated / body.rate) }));
  }
  if (body.padded) {
    bits.push(t("trim.padded", { amount: seconds(body.padded / body.rate) }));
  }
  unstage();
  $("fit-done").textContent = bits.join(" ");
  await refresh();
}

async function annotate(payload) {
  await fetch(`/api/note/${chosen.kind}/${chosen.index}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await refresh();
}

async function refresh() {
  state = await (await fetch("/api/map")).json();
  render();
}

// ====================================================================
//  The music
// ====================================================================

let rendering = false;
let bulkStop = false;
let bulkAbort = null;
let watching = null;

/** Follows one render while it happens.
 *
 * The work is seconds of emulation inside a request that has not
 * answered yet; the server reports where it has got to on its own
 * endpoint, which a threaded server can serve meanwhile.
 */
function watchProgress(on) {
  clearInterval(watching);
  watching = null;
  $("inner-progress").hidden = !on;
  if (!on) return;
  watching = setInterval(async () => {
    let state_;
    try {
      state_ = await (await fetch("/api/progress")).json();
    } catch (ignored) { return; }
    if (!state_ || !state_.total) return;
    const share = Math.min(1, state_.done / state_.total);
    $("inner-bar").querySelector("i").style.width = `${share * 100}%`;
    $("inner-what").textContent = t("music.stage." + state_.stage, {
      cmd: "$" + (state_.command || 0).toString(16).toUpperCase().padStart(2, "0"),
    });
    $("inner-count").textContent = t("music.inner", {
      done: state_.done.toFixed(1), total: state_.total,
    });
  }, 250);
}

async function loadMusic() {
  const body = await (await fetch("/api/music")).json();
  musicRows = body.commands || [];
  musicCapacity = body.capacity || {};
  drawChipChoice();
  drawMusic();
  musicLoaded = true;
}

function drawMusic() {
  const loudest = musicRows.length ? musicRows[0].notes : 1;
  $("music-count").textContent = t("music.count", { n: musicRows.length });
  $("music-list").innerHTML = musicRows.map((row) => `
      <li data-cmd="${row.command}" class="${row.ready ? "ready" : ""} ${
        row.marked ? "marked" : ""}">
        <span class="cmd" style="${row.colour ? `color:${row.colour}` : ""}">$${
          row.command.toString(16).toUpperCase().padStart(2, "0")}</span>
        <span class="notes">${row.note || t("music.notes", { n: row.notes })}</span>
        <span class="bar"><i style="width:${(row.notes / loudest) * 100}%"></i></span>
        <span class="plays">${row.samples.length
          ? t("music.plays", { samples: row.samples.join(", ") }) : ""}</span>
        <span class="hint">${row.ready ? t("music.readyHint") : ""}</span>
        <button><i class="fa-solid fa-play"></i>${
          t(row.ready ? "music.ready" : "music.render")}</button>
      </li>`).join("");

  for (const item of $("music-list").querySelectorAll("li")) {
    const command = Number(item.dataset.cmd);
    item.addEventListener("click", () => selectMusic(command, true));
    item.querySelector("button").addEventListener("click", (event) => {
      event.stopPropagation();
      selectMusic(command, true);
      playMusic(item);
    });
  }
  if (chosenMusic) selectMusic(chosenMusic.command, true);
}

function musicRow(command) {
  return musicRows.find((row) => row.command === command) || null;
}

function selectMusic(command, quiet) {
  chosenMusic = musicRow(command);
  if (!chosenMusic) return;
  for (const item of $("music-list").querySelectorAll("li.sel")) {
    item.classList.remove("sel");
  }
  const item = $("music-list").querySelector(`li[data-cmd="${command}"]`);
  if (item) item.classList.add("sel");

  const hex = "$" + command.toString(16).toUpperCase().padStart(2, "0");
  $("music-hint").hidden = true;
  $("music-chosen").hidden = false;
  $("music-title").textContent = hex;
  $("music-facts").innerHTML = [
    [t("music.command"), hex],
    [t("music.notesLabel"), chosenMusic.notes],
    [t("fact.commands"), chosenMusic.samples.join(", ") || "—"],
  ].map(([term, value]) => `<dt>${term}</dt><dd>${value}</dd>`).join("");
  $("music-save").href = `/api/music/${command}`;
  $("music-revert").hidden = !chosenMusic.replaced;
  if (!quiet || !mstaged || mstaged.index !== command) unstageMusic();
  if (!quiet || !midiInfo || midiInfo.index !== command) unstageMidi();
  $("mfit-done").textContent = chosenMusic.replaced
    ? t("mrep.replaced", { list: chosenMusic.replaced.join(", ") }) : "";
  $("music-marked").checked = chosenMusic.marked;
  $("music-note").value = chosenMusic.note || "";
  $("music-colour").value = chosenMusic.colour || "#b07fd4";
  categoryChips($("music-category"), chosenMusic.category || "",
                (cat) => annotateMusic({ category: cat }));
}

function lockMusic(busy) {
  rendering = busy;
  for (const other of $("music-list").querySelectorAll("button")) other.disabled = busy;
  $("render-all").disabled = busy;
}

async function playMusic(item) {
  const command = Number(item.dataset.cmd);
  if (rendering) return;
  const row = musicRow(command);

  if (row && row.ready) {
    sound(`/api/music/${command}`, $("music-wave"));
    return;
  }

  const button = item.querySelector("button");
  lockMusic(true);
  button.innerHTML = `<i class="spinner"></i>${t("music.rendering")}`;
  item.classList.add("working");
  watchProgress(true);
  try {
    const response = await fetch(`/api/music/${command}`);
    if (!response.ok) {
      // Only a 404 means the command really made no music; anything
      // else (no ROMs loaded after a restart, a server error) is shown
      // as itself rather than blamed on the command.
      const body = await response.json().catch(() => ({}));
      button.textContent = response.status === 404 ? t("music.failed")
        : (body.error || t("music.serverError", { status: response.status }));
      return;
    }
    const url = URL.createObjectURL(await response.blob());
    if (row) row.ready = true;
    item.classList.add("ready");
    item.querySelector(".hint").textContent = t("music.readyHint");
    button.innerHTML = `<i class="fa-solid fa-play"></i>${t("music.ready")}`;
    sound(url, $("music-wave"));
  } catch (problem) {
    button.textContent = t("music.noServer");
  } finally {
    watchProgress(false);
    item.classList.remove("working");
    lockMusic(false);
  }
}

async function renderAll() {
  const pending = musicRows.filter((row) => !row.ready);
  if (!pending.length) return;
  bulkStop = false;
  bulkAbort = new AbortController();
  $("render-stop").hidden = false;
  $("render-stop").disabled = false;
  $("render-stop").innerHTML =
    `<i class="fa-solid fa-stop"></i>${t("music.stopAll")}`;
  $("bulk-bar").hidden = false;
  $("bulk-bar").querySelector("i").style.width = "0%";
  lockMusic(true);

  let done = 0;
  const progress = () => {
    $("bulk-count").textContent = t("music.bulk", {
      done: done, total: pending.length, left: pending.length - done,
    });
    $("bulk-bar").querySelector("i").style.width =
      `${(done / pending.length) * 100}%`;
  };
  progress();

  watchProgress(true);
  for (const row of pending) {
    if (bulkStop) break;
    const item = $("music-list").querySelector(`li[data-cmd="${row.command}"]`);
    if (item) item.classList.add("working");
    try {
      const response = await fetch(`/api/music/${row.command}`,
                                   { signal: bulkAbort.signal });
      if (response.ok) {
        row.ready = true;
        if (item) {
          item.classList.add("ready");
          item.querySelector(".hint").textContent = t("music.readyHint");
        }
        // Play it straight away. The next render takes far longer than
        // a track lasts, so this costs the batch nothing and gives the
        // wait something to listen to.
        if ($("bulk-play").checked) {
          selectMusic(row.command, true);
          sound(URL.createObjectURL(await response.blob()), $("music-wave"));
        }
      }
    } catch (problem) {
      // An abort is the user saying stop, not a failure to report.
      if (problem.name === "AbortError") {
        if (item) item.classList.remove("working");
        break;
      }
      /* one failure is not the batch */
    }
    if (item) item.classList.remove("working");
    done += 1;
    progress();
  }

  watchProgress(false);
  bulkAbort = null;
  $("render-stop").hidden = true;
  lockMusic(false);
}

// -- replacing the music with a recording ----------------------------

let mstaged = null;
let mmode = "auto";
let mchip = "dac";
let mfile = null;
let musicCapacity = {};

function drawChipChoice() {
  for (const button of $("mchip").querySelectorAll("button")) {
    const chip = button.dataset.chip;
    button.classList.toggle("on", chip === mchip);
    const room = musicCapacity[chip];
    $("mroom-" + chip).textContent = room ? t("mrep.room", {
      free: seconds(room.free_seconds), total: seconds(room.total_seconds),
    }) : "";
  }
  $("mblocks-label").textContent = t("mrep.blocks", { chip: mchip.toUpperCase() });
}

const hex2 = (n) => "$" + n.toString(16).toUpperCase().padStart(2, "0");

function musicBlocks() {
  return $("mblocks").value.split(/[\s,;]+/).filter(Boolean)
    .map((text) => Number(text.replace(/^(cvsd|dac):/i, ""))).filter(Number.isInteger);
}

function musicQuery() {
  return `blocks=${musicBlocks().join(",")}&offset=${
    Number($("moffset").value)}&mode=${mmode}&chip=${mchip}`;
}

async function stageMusic(file) {
  const command = chosenMusic.command;
  mfile = file;
  const response = await fetch(`/api/music/${command}/stage?chip=${mchip}`, {
    method: "POST", body: await file.arrayBuffer(),
  });
  const body = await response.json();
  $("mtrim").hidden = false;
  if (body.error) {
    $("mfit").className = "fit warn";
    $("mfit").textContent = body.error;
    return;
  }
  mmode = "auto";
  $("moffset").value = 0;
  $("mblocks").value = body.blocks.join(", ");
  $("mfit-done").textContent = "";
  showMusicFit(body);
  drawMusicModes();
  hearMusic();
}

function showMusicFit(body) {
  mstaged = body;
  $("moffset").max = body.max_offset;
  $("moffset-wrap").hidden = body.mode !== "trim" || body.max_offset === 0;
  const bits = [t("mrep.fit", { source: seconds(body.source_seconds),
                                capacity: seconds(body.capacity_seconds) })];
  if (body.mode === "speed") {
    bits.push(t("mode.speedInfo", { pct: (100 * body.speed).toFixed(0) }));
  }
  $("mfit").className = body.truncated ? "fit warn" : "fit";
  $("mfit").textContent = bits.join(" ");
  $("mtrim-label").textContent = body.max_offset === 0 ? t("trim.exact")
    : t(body.longer ? "trim.startAt" : "trim.silenceBefore",
        { at: seconds(Number($("moffset").value) / body.rate) });
  $("mtaken").innerHTML = body.taken.map((use) => {
    const who = [];
    if (use.commands.length) {
      who.push(t("mrep.commands", { list: use.commands.map(hex2).join(", ") }));
    }
    if (use.speech.length) {
      who.push(t("mrep.speech", { list: use.speech.map(hex2).join(", ") }));
    }
    return `<li class="${who.length ? "used" : "free"}">${who.length
      ? t("mrep.used", { chip: body.chip, i: use.index, who: who.join(" \u00b7 ") })
      : t("mrep.free", { chip: body.chip, i: use.index })}</li>`;
  }).join("");
  $("mshared").hidden = !body.shared.length;
  $("mshared").textContent = t("mrep.shared",
                               { list: body.shared.map(hex2).join(", ") });
}

async function refitMusic() {
  const response = await fetch(`/api/music/${chosenMusic.command}/fit?${musicQuery()}`);
  const body = await response.json();
  if (body.error) {
    $("mfit").className = "fit warn";
    $("mfit").textContent = body.error;
    return false;
  }
  showMusicFit(body);
  return true;
}

function drawMusicModes() {
  $("mmodes").innerHTML = ["auto", "trim", "loop", "speed"].map((name) =>
    `<button data-mode="${name}" class="${name === mmode ? "on" : ""}"
       title="${t("mode." + name + "Hint")}">${t("mode." + name)}</button>`).join("");
  for (const button of $("mmodes").querySelectorAll("button")) {
    button.addEventListener("click", async () => {
      mmode = button.dataset.mode;
      drawMusicModes();
      if (await refitMusic()) hearMusic();
    });
  }
}

function hearMusic() {
  if (!mstaged) return;
  sound(`/api/music/${chosenMusic.command}/preview?${musicQuery()}&t=${Date.now()}`,
        $("music-wave"));
}

function unstageMusic() {
  mstaged = null;
  $("mtrim").hidden = true;
  $("mfit").textContent = "";
}

async function applyMusic() {
  const command = chosenMusic.command;
  const body = await (await fetch(`/api/music/${command}/commit?${musicQuery()}`,
                                  { method: "POST" })).json();
  if (body.error) {
    $("mfit").className = "fit warn";
    $("mfit").textContent = body.error;
    return;
  }
  unstageMusic();
  await reloadMusic();
  await refresh();
  $("mfit-done").textContent = t("mrep.applied", {
    list: body.blocks.map((i) => `${body.chip}:${i}`).join(", ") });
}

async function revertMusic() {
  await fetch(`/api/music/${chosenMusic.command}/revert`, { method: "POST" });
  await reloadMusic();
  await refresh();
}


// -- music compiled from MIDI ------------------------------------------

let midiInfo = null;
let instrumentList = null;
let midiTimer = null;

async function loadInstruments() {
  if (instrumentList) return instrumentList;
  const body = await (await fetch("/api/instruments")).json();
  instrumentList = body.instruments || [];
  return instrumentList;
}

function midiQuery() {
  const inst = [...$("midi-parts").querySelectorAll("select")]
    .filter((s) => s.value !== "").map((s) => `${s.dataset.part}:${s.value}`).join(",");
  return `block=${encodeURIComponent($("midi-block").value.trim())}` +
    `&priority=${encodeURIComponent($("midi-priority").value.replace(/\s+/g, ""))}` +
    `&inst=${inst}&loop=${$("midi-loop").checked ? 1 : 0}` +
    `&drums=${$("midi-drums").checked ? 1 : 0}&fit=${$("midi-fitmode").value}`;
}

function midiError(text) {
  $("midi-warn").hidden = false;
  $("midi-warn").textContent = text;
}

async function stageMidi(file) {
  const command = chosenMusic.command;
  $("midi-done").textContent = "";
  const response = await fetch(`/api/midi/${command}/stage`, {
    method: "POST", body: await file.arrayBuffer(),
  });
  const body = await response.json();
  $("midi-box").hidden = false;
  $("midi-warn").hidden = true;
  if (body.error) { midiError(body.error); return; }
  midiInfo = body;
  const instruments = await loadInstruments();
  const notes = body.channels.reduce((sum, c) => sum + c.notes, 0);
  $("midi-summary").textContent = t("midi.summary", {
    seconds: seconds(body.seconds), notes: notes, channels: body.channels.length,
  });
  const options = instruments.map((i) =>
    `<option value="${i.id}">${t("midi.instrument", i)}</option>`).join("");
  $("midi-parts").innerHTML = body.parts.map((part) => `
      <li>
        <span class="part-name">${t("midi.part", { n: part.index + 1, ym: part.ym.join("+") })}</span>
        <div class="pick-row">
          <select data-part="${part.index}">
            <option value="">${t("midi.original")}${part.instrument === null ? "" : ` (${part.instrument})`}</option>
            ${options}
          </select>
          <button class="quiet audition" data-part="${part.index}" title="${t("midi.audition")}">
            <i class="fa-solid fa-play"></i></button>
        </div>
      </li>`).join("");
  for (const select of $("midi-parts").querySelectorAll("select")) {
    select.addEventListener("change", refitMidi);
  }
  for (const button of $("midi-parts").querySelectorAll("button.audition")) {
    button.addEventListener("click", () => auditionInstrument(button));
  }
  $("midi-priority").value = body.channels.filter((c) => !c.drums)
    .map((c) => c.channel).join(", ");
  $("midi-block").value = body.block || "";
  await refitMidi();
}

async function auditionInstrument(button) {
  const select = $("midi-parts").querySelector(`select[data-part="${button.dataset.part}"]`);
  const part = midiInfo.parts[Number(button.dataset.part)];
  const ident = select.value !== "" ? select.value : part.instrument;
  if (ident === null || ident === undefined || ident === "") return;
  const old = button.innerHTML;
  button.innerHTML = `<i class="spinner"></i>`;
  watchProgress(true);
  try {
    const response = await fetch(`/api/instrument/${ident}?cmd=${chosenMusic.command}`);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      midiError(body.error || String(response.status));
      return;
    }
    sound(URL.createObjectURL(await response.blob()), $("music-wave"));
  } finally {
    watchProgress(false);
    button.innerHTML = old;
  }
}

async function refitMidi() {
  if (!midiInfo) return;
  const body = await (await fetch(
    `/api/midi/${chosenMusic.command}/fit?${midiQuery()}`)).json();
  $("midi-warn").hidden = true;
  if (body.error) { midiError(body.error); return; }
  const fitting = [t("midi.fit", body)];
  if (body.cut_at !== null) {
    fitting.push(t("midi.cut", { at: seconds(body.cut_at), total: seconds(body.song_seconds) }));
  }
  if (body.removed.length) fitting.push(t("midi.removed", { list: body.removed.join(", ") }));
  if (body.cut_at === null && !body.removed.length && body.spare > 0) {
    fitting.push(t(body.loop ? "midi.spareLoop" : "midi.spare", body));
  }
  $("midi-fit").textContent = fitting.join(" ");
  const drums = midiInfo.channels.some((c) => c.drums);
  $("midi-channels").innerHTML = body.channels.map((c) =>
    `<li class="${c.placed === c.total ? "free" : "used"}">${t("midi.channel", {
      c: c.channel, placed: c.placed, total: c.total })}</li>`).join("") +
    (drums ? `<li>${t("midi.drumsChannel")}</li>` : "");
  const who = [];
  if (body.taken.commands.length) {
    who.push(t("mrep.commands", { list: body.taken.commands.map(hex2).join(", ") }));
  }
  if (body.taken.speech.length) {
    who.push(t("mrep.speech", { list: body.taken.speech.map(hex2).join(", ") }));
  }
  const warn = [who.length ? t("midi.taken", { who: who.join(" \u00b7 ") }) : t("midi.free")];
  if (body.shared.length) {
    warn.push(t("mrep.shared", { list: body.shared.map(hex2).join(", ") }));
  }
  $("midi-warn").hidden = false;
  $("midi-warn").textContent = warn.join(" ");
}

function scheduleRefit() {
  clearTimeout(midiTimer);
  midiTimer = setTimeout(refitMidi, 400);
}

async function hearMidi() {
  const button = $("midi-hear");
  const old = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<i class="spinner"></i>${t("midi.rendering")}`;
  watchProgress(true);
  try {
    const response = await fetch(`/api/midi/${chosenMusic.command}/preview?${midiQuery()}`);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      midiError(body.error || String(response.status));
      return;
    }
    sound(URL.createObjectURL(await response.blob()), $("music-wave"));
  } finally {
    watchProgress(false);
    button.disabled = false;
    button.innerHTML = old;
  }
}

async function applyMidi() {
  const body = await (await fetch(
    `/api/midi/${chosenMusic.command}/commit?${midiQuery()}`, { method: "POST" })).json();
  if (body.error) { midiError(body.error); return; }
  unstageMidi();
  await reloadMusic();
  await refresh();
  $("midi-done").textContent = t("midi.applied", { block: body.block });
}

function unstageMidi() {
  midiInfo = null;
  $("midi-box").hidden = true;
}

async function annotateMusic(payload) {
  if (!chosenMusic) return;
  await fetch(`/api/note/music/${chosenMusic.command}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await reloadMusic();
}

async function reloadMusic() {
  // Reloading the list loses what has been rendered this session, so
  // carry those flags across -- except for a command whose sound has
  // just changed under it, which the server reports as it is.
  const ready = new Map(musicRows.map((row) => [row.command, row.ready]));
  await loadMusic();
  for (const row of musicRows) {
    if (ready.get(row.command) && !row.replaced) row.ready = true;
  }
  drawMusic();
}

// ====================================================================
//  Tabs, flyer, notes, language
// ====================================================================

function showTab(which) {
  // Switching view silences whatever was playing: a sound coming from
  // a panel that is no longer on screen is only confusing.
  stopSound();
  $("workspace").hidden = which !== "map";
  $("music").hidden = which !== "music";
  $("tab-map").className = which === "map" ? "tab" : "tab quiet";
  $("tab-music").className = which === "music" ? "tab" : "tab quiet";
  if (which === "music" && !musicLoaded) loadMusic();
}

const FLYER_KEY = "wpcide.flyer";

function restoreFlyer() {
  try {
    const saved = localStorage.getItem(`${FLYER_KEY}.${state.game.key}`);
    if (saved) showFlyer(saved);
  } catch (ignored) { /* private mode */ }
}

function showFlyer(source) {
  $("flyer-img").src = source;
  $("flyer").classList.toggle("has-image", Boolean(source));
}

function setFlyer(file) {
  const reader = new FileReader();
  reader.onload = () => {
    showFlyer(reader.result);
    try {
      localStorage.setItem(`${FLYER_KEY}.${state.game.key}`, reader.result);
    } catch (ignored) { /* an image too big for the quota */ }
  };
  reader.readAsDataURL(file);
}

const NOTES_KEY = "wpcide.notes";

function mirror() {
  // A copy in the browser as well as the file on disk: a cookie cannot
  // hold two hundred annotations at 4 KB, and localStorage is what the
  // browser-only build will use when there is no file to write.
  try {
    const notes = {};
    for (const chip of state.chips) {
      for (const block of chip.blocks) {
        if (block.note || block.colour || block.category || block.marked) {
          notes[`${block.kind}:${block.index}`] = {
            note: block.note, colour: block.colour,
            category: block.category, marked: block.marked,
          };
        }
      }
    }
    localStorage.setItem(`${NOTES_KEY}.${state.game.key}`, JSON.stringify(notes));
  } catch (ignored) { /* quota or private mode: the file still has them */ }
}

async function importNotes(file) {
  let payload;
  try {
    payload = JSON.parse(await file.text());
  } catch (problem) {
    $("dropmsg").textContent = t("notes.badFile");
    return;
  }
  const response = await fetch("/api/notes", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (body.error) { $("fit-done").textContent = body.error; return; }
  $("fit-done").textContent = t("notes.imported", { n: body.imported });
  await refresh();
}

let findTimer = null;

async function find(term) {
  const body = await (await fetch(`/api/games?q=${encodeURIComponent(term)}`)).json();
  const known = body.games.map((game) => `
      <li>
        <span>${game.name}</span>
        <small>${t("find.known", { sockets: game.sockets.join(", ").toUpperCase() })}</small>
        <a href="${game.url}" target="_blank" rel="noopener">
          <i class="fa-solid fa-arrow-up-right-from-square"></i>${t("find.openIpdb")}</a>
      </li>`).join("");
  const anywhere = term
    ? `<li class="any"><a href="${body.ipdb_search}" target="_blank" rel="noopener"
         ><i class="fa-solid fa-magnifying-glass"></i>${
           t("find.searchIpdb", { term })}</a></li>`
    : "";
  $("found").innerHTML = known + anywhere;
}

// ====================================================================
//  Wiring
// ====================================================================

const zone = $("dropzone");
for (const name of ["dragenter", "dragover"]) {
  zone.addEventListener(name, (event) => {
    event.preventDefault();
    zone.classList.add("over");
  });
}
for (const name of ["dragleave", "drop"]) {
  zone.addEventListener(name, () => zone.classList.remove("over"));
}
zone.addEventListener("drop", (event) => {
  event.preventDefault();
  offer(event.dataTransfer.files);
});
$("files").addEventListener("change", (event) => offer(event.target.files));

$("play").addEventListener("click", () => play(false));
$("play-original").addEventListener("click", () => play(true));
$("stop").addEventListener("click", stopSound);
$("music-play").addEventListener("click", () => {
  const item = chosenMusic &&
    $("music-list").querySelector(`li[data-cmd="${chosenMusic.command}"]`);
  if (item) playMusic(item);
});
$("music-stop").addEventListener("click", stopSound);
seekFrom($("wave"));
seekFrom($("music-wave"));

$("replace").addEventListener("change", (event) => {
  if (event.target.files[0]) stage(event.target.files[0]);
  event.target.value = "";
});
$("offset").addEventListener("input", () => describe(Number($("offset").value)));
$("offset").addEventListener("change", async () => { await refit(); hear(); });
$("hear").addEventListener("click", hear);
$("apply").addEventListener("click", apply);
$("cancel").addEventListener("click", unstage);
$("revert").addEventListener("click", async () => {
  await fetch(`/api/revert/${chosen.kind}/${chosen.index}`, { method: "POST" });
  $("fit-done").textContent = "";
  await refresh();
});

$("marked").addEventListener("change", (e) => annotate({ marked: e.target.checked }));
$("note").addEventListener("change", (e) => annotate({ note: e.target.value }));
$("colour").addEventListener("change", (e) => annotate({ colour: e.target.value }));
$("colour-clear").addEventListener("click", () => annotate({ colour: "" }));

$("music-marked").addEventListener("change",
  (e) => annotateMusic({ marked: e.target.checked }));
$("music-note").addEventListener("change", (e) => annotateMusic({ note: e.target.value }));
$("music-colour").addEventListener("change",
  (e) => annotateMusic({ colour: e.target.value }));
$("music-colour-clear").addEventListener("click", () => annotateMusic({ colour: "" }));
$("render-all").addEventListener("click", renderAll);
$("music-replace").addEventListener("change", (event) => {
  if (event.target.files[0] && chosenMusic) stageMusic(event.target.files[0]);
  event.target.value = "";
});
$("mblocks").addEventListener("change", async () => {
  if (mstaged && await refitMusic()) hearMusic();
});
$("moffset").addEventListener("change", async () => {
  if (await refitMusic()) hearMusic();
});
$("mhear").addEventListener("click", hearMusic);
for (const button of $("mchip").querySelectorAll("button")) {
  button.addEventListener("click", () => {
    mchip = button.dataset.chip;
    drawChipChoice();
    // A staged WAV is re-fitted to the other chip with fresh blocks:
    // the indices of one table mean nothing in the other.
    if (mstaged && mfile) stageMusic(mfile);
  });
}
$("mapply").addEventListener("click", applyMusic);
$("mcancel").addEventListener("click", unstageMusic);
$("music-revert").addEventListener("click", revertMusic);
$("midi-pick").addEventListener("change", (event) => {
  if (event.target.files[0] && chosenMusic) stageMidi(event.target.files[0]);
  event.target.value = "";
});
for (const id of ["midi-priority", "midi-block"]) {
  $(id).addEventListener("input", scheduleRefit);
}
for (const id of ["midi-loop", "midi-drums", "midi-fitmode"]) {
  $(id).addEventListener("change", refitMidi);
}
$("midi-hear").addEventListener("click", hearMidi);
$("midi-apply").addEventListener("click", applyMidi);
$("midi-cancel").addEventListener("click", unstageMidi);
$("render-stop").addEventListener("click", (event) => {
  bulkStop = true;
  // The one already running keeps going on the server and lands in the
  // cache; what stops here is waiting for it.
  if (bulkAbort) bulkAbort.abort();
  event.currentTarget.disabled = true;
  event.currentTarget.innerHTML =
    `<i class="spinner"></i>${t("music.stopping")}`;
});

$("bundle").addEventListener("click", () => { window.location = "/api/bundle"; });
$("bundle-marked").addEventListener("click", () => {
  window.location = "/api/bundle?marked=1";
});
$("notes-export").addEventListener("click", () => { window.location = "/api/notes"; });
$("notes-import").addEventListener("change", (event) => {
  if (event.target.files[0]) importNotes(event.target.files[0]);
  event.target.value = "";
});
$("flyer-pick").addEventListener("change", (event) => {
  if (event.target.files[0]) setFlyer(event.target.files[0]);
  event.target.value = "";
});
$("flyer-close").addEventListener("click", () => { $("flyer").hidden = true; });

$("flyer-url").addEventListener("click", async () => {
  const url = prompt(t("flyer.askUrl"));
  if (!url) return;
  const response = await fetch("/api/fetch?url=" + encodeURIComponent(url));
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    $("dropmsg").textContent = body.error || String(response.status);
    return;
  }
  const reader = new FileReader();
  reader.onload = () => showFlyer(reader.result);
  reader.readAsDataURL(await response.blob());
});

// -- network settings: the headers the fetch sends ---------------------

$("settings-open").addEventListener("click", async () => {
  const values = await (await fetch("/api/settings")).json();
  $("set-ua").value = values.user_agent || "";
  $("set-referer").value = values.referer || "";
  $("set-timeout").value = values.timeout;
  $("settings").showModal();
});
$("set-save").addEventListener("click", async (event) => {
  event.preventDefault();
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_agent: $("set-ua").value,
      referer: $("set-referer").value || null,
      timeout: Number($("set-timeout").value) || 20,
    }),
  });
  $("settings").close();
});
$("set-close").addEventListener("click", (event) => {
  event.preventDefault();
  $("settings").close();
});


$("tab-map").addEventListener("click", () => showTab("map"));
$("tab-music").addEventListener("click", () => showTab("music"));
$("find").addEventListener("input", (event) => {
  clearTimeout(findTimer);
  const term = event.target.value;
  findTimer = setTimeout(() => find(term), 180);
});
$("lang").addEventListener("click", () => {
  setLanguage(document.documentElement.lang === "es" ? "en" : "es");
  musicLoaded = false;
  if (state) {
    const onMusic = !$("music").hidden;
    render();
    showTab(onMusic ? "music" : "map");
  } else {
    find($("find").value);
    fetch("/api/status").then((r) => r.json()).then(drawSlots);
  }
});

setLanguage(document.documentElement.lang);
find("");
for (const button of $("board-pick").querySelectorAll("button")) {
  button.addEventListener("click", async () => {
    const response = await fetch(`/api/board?platform=${button.dataset.board}`, { method: "POST" });
    drawSlots(await response.json());
  });
}
fetch("/api/status").then((r) => r.json()).then(drawSlots);

// The ROM lives in the process, not the page, so a reload should find
// the session already loaded rather than asking for the files again.
(async () => {
  const response = await fetch("/api/map");
  if (!response.ok) return;
  state = await response.json();
  render();
})();
