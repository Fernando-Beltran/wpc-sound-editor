"use strict";

// ====================================================================
//  DCS: a track list, and a trimmer that works on the waveform
// ====================================================================
//
// A DCS game has no chips to draw -- DCS Explorer reads its catalogue --
// so the view is the tracks, by channel. Replacing one re-programs the
// track around the user's audio; the part that matters to a person is
// choosing *which* part of a long file goes in, so the file is decoded
// here, drawn large, and cut with a window the size of the slot that
// can be dragged along it and heard as it moves.

let dcsChannel = "all";
let dcsChosen = null;
let dcsSource = null;       // { buffer, mono, rate, peaks, name }
let dcsSel = null;          // { start, end } in seconds
let dcsDrag = null;         // { mode, grab, start, end }
let dcsPlaying = false;

function dcsRow(command) {
  return (state.tracks || []).find((row) => row.command === command) || null;
}

function renderDcs() {
  $("tabs").hidden = true;
  $("workspace").hidden = true;
  $("music").hidden = true;
  $("dcs").hidden = false;
  drawDcsChannels();
  drawDcsList();
  drawDcsChips();
  if (dcsChosen) selectDcs(dcsChosen.command, true);
}

/** The seven sound ROMs, drawn like the pre-DCS map, streams in place. */
function drawDcsChips() {
  $("dcs-chips").innerHTML = (state.chips || []).map(chipHtml).join("");
  for (const button of $("dcs-chips").querySelectorAll(".block")) {
    const address = Number(button.dataset.index);
    button.addEventListener("click", () => {
      // A stream belongs to the tracks that play it: pick the first,
      // or keep the current one if it is among them.
      const owners = state.tracks.filter((row) => row.stream_ids.includes(address));
      if (!owners.length) return;
      const keep = dcsChosen && owners.some((row) => row.command === dcsChosen.command);
      const row = keep ? dcsChosen : owners[0];
      if (dcsChannel !== "all" && String(row.channel) !== dcsChannel) {
        dcsChannel = "all";
        drawDcsChannels();
      }
      $("dcs-search").value = "";
      drawDcsList();
      selectDcs(row.command);
      const item = $("dcs-list").querySelector(`li[data-cmd="${row.command}"]`);
      if (item) item.scrollIntoView({ block: "nearest" });
    });
  }
}

function lightDcsChips(row) {
  const mine = new Set(row ? row.stream_ids : []);
  for (const button of $("dcs-chips").querySelectorAll(".block")) {
    button.classList.toggle("hit", mine.has(Number(button.dataset.index)));
  }
  const first = $("dcs-chips").querySelector(".block.hit");
  if (first) first.scrollIntoView({ block: "nearest" });
}

function drawDcsChannels() {
  const counts = {};
  for (const row of state.tracks) counts[row.channel] = (counts[row.channel] || 0) + 1;
  const options = [["all", t("dcs.all", { n: state.tracks.length })]].concat(
    Object.keys(counts).sort().map((c) => [String(c), t(c === "0" ? "dcs.channelMusic"
      : "dcs.channel", { c: c, n: counts[c] })]));
  $("dcs-channels").innerHTML = options.map(([value, text]) =>
    `<button class="${value === dcsChannel ? "on" : ""}" data-ch="${value}">${text}</button>`).join("");
  for (const button of $("dcs-channels").querySelectorAll("button")) {
    button.addEventListener("click", () => {
      dcsChannel = button.dataset.ch;
      drawDcsChannels();
      drawDcsList();
    });
  }
}

function drawDcsList() {
  const needle = $("dcs-search").value.trim().toLowerCase();
  const rows = state.tracks.filter((row) =>
    (dcsChannel === "all" || String(row.channel) === dcsChannel) &&
    (!needle || row.label.toLowerCase().includes(needle) ||
     row.label.replace(" ", "").toLowerCase().includes(needle) ||
     (row.note || "").toLowerCase().includes(needle)));
  $("dcs-count").textContent = t("dcs.count", { n: rows.length });
  $("dcs-list").innerHTML = rows.map((row) => `
      <li data-cmd="${row.command}" class="${row.edited ? "edited" : ""} ${
        row.marked ? "marked" : ""} ${dcsChosen && dcsChosen.command === row.command ? "sel" : ""}">
        <span class="cmd" style="${row.colour ? `color:${row.colour}` : ""}">${row.label}</span>
        <span class="ch">${t("dcs.ch", { c: row.channel })}</span>
        <span class="len">${row.seconds.toFixed(2)} s${row.loop ? ' <i class="fa-solid fa-repeat" title="' + t("dcs.loops") + '"></i>' : ""}</span>
        <span class="notes">${row.note || ""}</span>
        ${row.edited ? '<i class="fa-solid fa-pen edited-mark"></i>' : ""}
      </li>`).join("");
  for (const item of $("dcs-list").querySelectorAll("li")) {
    item.addEventListener("click", () => selectDcs(Number(item.dataset.cmd)));
  }
}

function selectDcs(command, quiet) {
  const row = dcsRow(command);
  if (!row) return;
  const changed = !dcsChosen || dcsChosen.command !== command;
  dcsChosen = row;
  for (const item of $("dcs-list").querySelectorAll("li")) {
    item.classList.toggle("sel", Number(item.dataset.cmd) === command);
  }
  $("dcs-hint").hidden = true;
  $("dcs-chosen").hidden = false;
  $("dcs-title").textContent = row.label;
  $("dcs-facts").innerHTML = [
    [t("dcs.channelLabel"), row.channel + (row.channel === 0 ? " · " + t("dcs.music") : "")],
    [t("dcs.length"), `${row.seconds.toFixed(2)} s${row.loop ? " · " + t("dcs.loops") : ""}`],
    [t("dcs.streams"), row.streams],
    [t("dcs.shares"), row.shared.length ? row.shared.join(", ") : "—"],
  ].map(([term, value]) => `<dt>${term}</dt><dd>${value}</dd>`).join("");
  $("dcs-revert").hidden = !row.edited;
  lightDcsChips(row);
  $("dcs-note").value = row.note || "";
  $("dcs-marked").checked = row.marked;
  $("dcs-slot").textContent = `${row.seconds.toFixed(2)} s`;
  if (changed && !quiet) {
    dcsDiscard();
    $("dcs-done").textContent = row.edited ? t("dcs.edited") : "";
  }
}

function playDcs(original) {
  if (!dcsChosen) return;
  stopSelection();
  setRate($("player"), 1, true);
  sound(`/api/dcs/wav/${dcsChosen.command}?t=${Date.now()}${original ? "&original=1" : ""}`,
        $("dcs-wave"));
}

// -- the trimmer -------------------------------------------------------

async function loadSource(file) {
  const context = new (window.AudioContext || window.webkitAudioContext)();
  let buffer;
  try {
    buffer = await context.decodeAudioData(await file.arrayBuffer());
  } catch (problem) {
    $("dcs-report").textContent = t("dcs.undecodable");
    context.close();
    return;
  }
  context.close();
  // Mono, as the board plays it: the average of the channels.
  const mono = new Float32Array(buffer.length);
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    const data = buffer.getChannelData(c);
    for (let i = 0; i < data.length; i++) mono[i] += data[i] / buffer.numberOfChannels;
  }
  dcsSource = { buffer, mono, rate: buffer.sampleRate, name: file.name,
                url: URL.createObjectURL(file), peaks: monoPeaks(mono, 1200) };
  const length = Math.min(dcsChosen.seconds, buffer.duration);
  const start = Math.min(firstSound(mono, buffer.sampleRate), buffer.duration - length);
  dcsSel = { start: Math.max(0, start), end: Math.max(0, start) + length };
  $("dcs-trim").hidden = false;
  $("dcs-trim-title").textContent = t("dcs.trimTitle", { label: dcsChosen.label });
  $("dcs-trim").scrollIntoView({ block: "nearest" });
  $("dcs-report").textContent = t("dcs.loaded", {
    name: file.name, seconds: buffer.duration.toFixed(2) });
  syncTimes();
  drawSource();
}

function monoPeaks(mono, count) {
  const per = Math.max(1, Math.floor(mono.length / count));
  const out = new Float32Array(count);
  let loudest = 0;
  for (let i = 0; i < count; i++) {
    let top = 0;
    for (let j = i * per; j < (i + 1) * per && j < mono.length; j++) {
      const v = Math.abs(mono[j]);
      if (v > top) top = v;
    }
    out[i] = top;
    loudest = Math.max(loudest, top);
  }
  if (loudest) for (let i = 0; i < count; i++) out[i] /= loudest;
  return out;
}

/** Where the sound starts: the first 50 ms window above a tenth of the loudest. */
function firstSound(mono, rate) {
  const step = Math.floor(rate / 20);
  let loudest = 0;
  const levels = [];
  for (let i = 0; i < mono.length; i += step) {
    let sum = 0;
    for (let j = i; j < i + step && j < mono.length; j++) sum += Math.abs(mono[j]);
    levels.push(sum / step);
    loudest = Math.max(loudest, sum / step);
  }
  const at = levels.findIndex((v) => v > 0.1 * loudest);
  return at > 0 ? Math.max(0, at / 20 - 0.05) : 0;
}

function drawSource() {
  const canvas = $("dcs-src");
  if (!dcsSource) return;
  const pen = canvas.getContext("2d");
  const { width, height } = canvas;
  const duration = dcsSource.buffer.duration;
  const x = (seconds) => (seconds / duration) * width;
  pen.clearRect(0, 0, width, height);
  // Outside the window dimmed, inside bright: what goes in is what lights up.
  const middle = height / 2;
  const step = width / dcsSource.peaks.length;
  const a = x(dcsSel.start), b = x(dcsSel.end);
  for (let i = 0; i < dcsSource.peaks.length; i++) {
    const px = i * step;
    const h = Math.max(1, dcsSource.peaks[i] * (middle - 4));
    pen.fillStyle = px >= a && px <= b ? "#4aa3df" : "#3a4150";
    pen.fillRect(px, middle - h, Math.max(1, step), h * 2);
  }
  pen.fillStyle = "rgba(74, 163, 223, 0.12)";
  pen.fillRect(a, 0, b - a, height);
  pen.fillStyle = "#e2a03f";
  pen.fillRect(a - 1, 0, 3, height);
  pen.fillRect(b - 1, 0, 3, height);
  pen.fillRect(a - 6, 0, 13, 8);
  pen.fillRect(b - 6, height - 8, 13, 8);
  const player = $("player");
  if (dcsPlaying && player.src === dcsSource.url) {
    pen.fillStyle = "#ffffff";
    pen.fillRect(x(player.currentTime) - 1, 0, 2, height);
  }
}

/** How fast the selection has to play to fill the slot (1 when not fitting). */
function dcsSpeed() {
  if (!dcsSel || $("dcs-fit").value === "none") return 1;
  return (dcsSel.end - dcsSel.start) / dcsChosen.seconds;
}

function setRate(player, speed, keepPitch) {
  player.playbackRate = speed;
  for (const name of ["preservesPitch", "mozPreservesPitch", "webkitPreservesPitch"]) {
    if (name in player) player[name] = keepPitch;
  }
}

/**
 * Time stretch that keeps the pitch: WSOLA. 50 ms Hann windows are laid
 * down half a window apart in the output; each is read from the input
 * near where the speed says it should be, nudged to wherever the
 * waveform best continues the previous window, so the joins stay in
 * phase instead of beating.
 */
function wsola(input, speed, rate) {
  const size = 2 * Math.round(rate * 0.025);
  const hop = size / 2;
  const tolerance = Math.round(rate * 0.012);
  const length = Math.floor(input.length / speed);
  const output = new Float32Array(length + size);
  const weight = new Float32Array(length + size);
  const window = new Float32Array(size);
  for (let i = 0; i < size; i++) window[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / size);
  let previous = 0;
  for (let k = 0; k * hop < length; k++) {
    const ideal = Math.round(k * hop * speed);
    let position = Math.min(ideal, input.length - size);
    if (k > 0) {
      const natural = previous + hop;
      let best = -Infinity;
      const low = Math.max(0, ideal - tolerance);
      const high = Math.min(input.length - size, ideal + tolerance);
      for (let candidate = low; candidate <= high; candidate += 2) {
        let score = 0;
        for (let i = 0; i < hop; i += 4) {
          const a = input[natural + i] || 0;
          score += a * input[candidate + i];
        }
        if (score > best) { best = score; position = candidate; }
      }
    }
    if (position < 0) break;
    const at = k * hop;
    for (let i = 0; i < size; i++) {
      output[at + i] += (input[position + i] || 0) * window[i];
      weight[at + i] += window[i];
    }
    previous = position;
  }
  for (let i = 0; i < length; i++) if (weight[i] > 1e-3) output[i] /= weight[i];
  return output.subarray(0, length);
}

/** Speed change that moves the pitch with it: a plain resample. */
function respeed(input, speed) {
  const length = Math.floor(input.length / speed);
  const output = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const at = i * speed;
    const j = Math.floor(at);
    const frac = at - j;
    output[i] = (input[j] || 0) * (1 - frac) + (input[j + 1] || 0) * frac;
  }
  return output;
}

function syncTimes() {
  $("dcs-start").value = dcsSel.start.toFixed(2);
  $("dcs-end").value = dcsSel.end.toFixed(2);
  const length = dcsSel.end - dcsSel.start;
  $("dcs-len").textContent = `${length.toFixed(2)} s`;
  const slot = dcsChosen.seconds;
  const fitting = $("dcs-fit").value !== "none";
  $("dcs-len").className = fitting || Math.abs(length - slot) < 0.05 ? "ok"
    : length > slot ? "warn" : "";
  $("dcs-speed").textContent = fitting
    ? t("dcs.speed", { pct: Math.round(100 * dcsSpeed()), slot: slot.toFixed(2) })
    : t("dcs.speedNone", { len: length.toFixed(2), slot: slot.toFixed(2) });
  if (dcsPlaying) setRate($("player"), dcsSpeed(), $("dcs-fit").value !== "pitch");
}

function clampSel(start, end) {
  const duration = dcsSource.buffer.duration;
  const length = end - start;
  if (start < 0) { start = 0; end = length; }
  if (end > duration) { end = duration; start = Math.max(0, duration - length); }
  dcsSel = { start: Math.max(0, start), end: Math.min(duration, Math.max(end, start + 0.05)) };
}

function timeAt(event) {
  const box = $("dcs-src").getBoundingClientRect();
  return Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)) *
    dcsSource.buffer.duration;
}

function grabSource(event) {
  if (!dcsSource) return;
  const box = $("dcs-src").getBoundingClientRect();
  const perPixel = dcsSource.buffer.duration / box.width;
  const at = timeAt(event);
  const near = 7 * perPixel;
  let mode = "move";
  if (Math.abs(at - dcsSel.start) < near) mode = "start";
  else if (Math.abs(at - dcsSel.end) < near) mode = "end";
  else if (at < dcsSel.start || at > dcsSel.end) {
    // A click outside moves the window there, then it can be dragged on.
    const length = dcsSel.end - dcsSel.start;
    clampSel(at, at + length);
  }
  if ($("dcs-lock").checked && mode !== "move") mode = "move";
  dcsDrag = { mode, grab: at, start: dcsSel.start, end: dcsSel.end };
  syncTimes();
  drawSource();
  event.preventDefault();
}

function dragSource(event) {
  if (!dcsDrag) {
    if (!dcsSource) return;
    const box = $("dcs-src").getBoundingClientRect();
    const at = timeAt(event);
    const near = 7 * dcsSource.buffer.duration / box.width;
    const edge = Math.abs(at - dcsSel.start) < near || Math.abs(at - dcsSel.end) < near;
    $("dcs-src").style.cursor = edge && !$("dcs-lock").checked ? "ew-resize"
      : at >= dcsSel.start && at <= dcsSel.end ? "grab" : "pointer";
    return;
  }
  const delta = timeAt(event) - dcsDrag.grab;
  if (dcsDrag.mode === "move") clampSel(dcsDrag.start + delta, dcsDrag.end + delta);
  else if (dcsDrag.mode === "start") clampSel(Math.min(dcsDrag.start + delta, dcsSel.end - 0.05), dcsSel.end);
  else clampSel(dcsSel.start, Math.max(dcsDrag.end + delta, dcsSel.start + 0.05));
  syncTimes();
  drawSource();
}

function dropSource() {
  if (!dcsDrag) return;
  dcsDrag = null;
  // Let go, and hear what the window now holds.
  playSelection();
}

function playSelection() {
  if (!dcsSource) return;
  const player = listen();
  if (player.src !== dcsSource.url) player.src = dcsSource.url;
  player.currentTime = dcsSel.start;
  // Heard at the speed it will have, so fitting it is done by ear.
  setRate(player, dcsSpeed(), $("dcs-fit").value !== "pitch");
  player.play().catch(() => {});
  dcsPlaying = true;
  const frame = () => {
    if (!dcsPlaying) return;
    if (player.currentTime >= dcsSel.end || player.currentTime < dcsSel.start - 0.25) {
      if ($("dcs-loop").checked && !player.paused) {
        player.currentTime = dcsSel.start;
      } else if (player.currentTime >= dcsSel.end) {
        player.pause();
        dcsPlaying = false;
      }
    }
    drawSource();
    if (dcsPlaying) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

function stopSelection() {
  setRate($("player"), 1, true);
  if (!dcsPlaying) return;
  dcsPlaying = false;
  $("player").pause();
  drawSource();
}

/** The selection as a 16-bit mono WAV at the file's own rate, fitted if asked. */
function selectionWav() {
  const rate = dcsSource.rate;
  const from = Math.floor(dcsSel.start * rate);
  const to = Math.min(dcsSource.mono.length, Math.floor(dcsSel.end * rate));
  let samples = dcsSource.mono.subarray(from, to);
  const speed = dcsSpeed();
  if (Math.abs(speed - 1) > 0.001) {
    samples = $("dcs-fit").value === "keep" ? wsola(samples, speed, rate) : respeed(samples, speed);
  }
  const count = samples.length;
  const fade = Math.min(Math.floor(rate * 0.01), Math.floor(count / 4));
  const view = new DataView(new ArrayBuffer(44 + count * 2));
  const text = (at, s) => { for (let i = 0; i < s.length; i++) view.setUint8(at + i, s.charCodeAt(i)); };
  text(0, "RIFF"); view.setUint32(4, 36 + count * 2, true); text(8, "WAVE");
  text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i++) {
    // Ten-millisecond fades at the cut, so it does not click.
    let gain = 1;
    if (i < fade) gain = i / fade;
    else if (i > count - fade) gain = (count - i) / fade;
    const v = Math.max(-1, Math.min(1, samples[i] * gain));
    view.setInt16(44 + i * 2, Math.round(v * 32767), true);
  }
  return view.buffer;
}

async function hearInRom() {
  if (!dcsSource) return;
  stopSelection();
  const button = $("dcs-hear");
  const old = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<i class="spinner"></i>${t("dcs.encoding")}`;
  try {
    const response = await fetch(`/api/dcs/${dcsChosen.command}/preview?match=${
      $("dcs-match").checked ? 1 : 0}`, { method: "POST", body: selectionWav() });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      $("dcs-report").textContent = body.error || String(response.status);
      return;
    }
    const report = JSON.parse(response.headers.get("X-Report") || "{}");
    $("dcs-report").textContent = t("dcs.previewed", { bitrate: report.bitrate });
    sound(URL.createObjectURL(await response.blob()), $("dcs-wave"));
  } finally {
    button.disabled = false;
    button.innerHTML = old;
  }
}

async function applyDcs() {
  if (!dcsSource) return;
  stopSelection();
  const button = $("dcs-apply");
  const old = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<i class="spinner"></i>${t("dcs.encoding")}`;
  try {
    const response = await fetch(`/api/dcs/${dcsChosen.command}/commit?match=${
      $("dcs-match").checked ? 1 : 0}`, { method: "POST", body: selectionWav() });
    const body = await response.json();
    if (body.error) { $("dcs-report").textContent = body.error; return; }
    const command = dcsChosen.command;
    dcsDiscard();
    await refresh();
    selectDcs(command, true);
    $("dcs-done").textContent = t("dcs.applied", {
      seconds: body.seconds.toFixed(2), bitrate: body.bitrate });
  } finally {
    button.disabled = false;
    button.innerHTML = old;
  }
}

function dcsDiscard() {
  stopSelection();
  dcsSource = null;
  dcsSel = null;
  $("dcs-trim").hidden = true;
  $("dcs-report").textContent = "";
}

async function revertDcs() {
  const command = dcsChosen.command;
  await fetch(`/api/dcs/${command}/revert`, { method: "POST" });
  await refresh();
  selectDcs(command, true);
  $("dcs-done").textContent = "";
}

async function annotateDcs(payload) {
  const command = dcsChosen.command;
  await fetch(`/api/note/dcs/${command}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await refresh();
  selectDcs(command, true);
}

function wireDcs() {
  $("dcs-search").addEventListener("input", drawDcsList);
  $("dcs-play").addEventListener("click", () => playDcs(false));
  $("dcs-original").addEventListener("click", () => playDcs(true));
  $("dcs-stop").addEventListener("click", () => { stopSelection(); stopSound(); });
  $("dcs-replace").addEventListener("change", (event) => {
    if (event.target.files[0] && dcsChosen) loadSource(event.target.files[0]);
    event.target.value = "";
  });
  const canvas = $("dcs-src");
  canvas.addEventListener("mousedown", grabSource);
  window.addEventListener("mousemove", dragSource);
  window.addEventListener("mouseup", dropSource);
  $("dcs-play-sel").addEventListener("click", playSelection);
  $("dcs-fit").addEventListener("change", () => {
    syncTimes();
    if (dcsSource) playSelection();
  });
  $("dcs-stop-sel").addEventListener("click", stopSelection);
  $("dcs-fit-slot").addEventListener("click", () => {
    clampSel(dcsSel.start, dcsSel.start + dcsChosen.seconds);
    syncTimes(); drawSource(); playSelection();
  });
  for (const id of ["dcs-start", "dcs-end"]) {
    $(id).addEventListener("change", () => {
      clampSel(Number($("dcs-start").value), Number($("dcs-end").value));
      syncTimes(); drawSource(); playSelection();
    });
  }
  $("dcs-hear").addEventListener("click", hearInRom);
  $("dcs-apply").addEventListener("click", applyDcs);
  $("dcs-cancel").addEventListener("click", dcsDiscard);
  $("dcs-revert").addEventListener("click", revertDcs);
  $("dcs-note").addEventListener("change", (e) => annotateDcs({ note: e.target.value }));
  $("dcs-marked").addEventListener("change", (e) => annotateDcs({ marked: e.target.checked }));
  seekFrom($("dcs-wave"));
}

wireDcs();
