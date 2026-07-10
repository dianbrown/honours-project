"use strict";

const $ = (id) => document.getElementById(id);

const screenHome = $("screenHome");
const screenLive = $("screenLive");
const readerPill = $("readerPill");
const startBtn = $("startBtn");
const exportBtn = $("exportBtn");
const exportCaption = $("exportCaption");
const liveCount = $("liveCount");
const liveTimer = $("liveTimer");
const pauseBtn = $("pauseBtn");
const stopBtn = $("stopBtn");
const pausedBanner = $("pausedBanner");
const tagList = $("tagList");
const emptyHint = $("emptyHint");
const stopConfirm = $("stopConfirm");
const exportPanel = $("exportPanel");
const settingsDrawer = $("settingsDrawer");
const toast = $("toast");
const debugLog = $("debugLog");
const homeClock = $("homeClock");

let state = null;
// Settings overrides applied to the next /api/start call.
const overrides = { uri: "", antenna: "", read_power: "", session_name: "" };

let renderedSession = "";
let renderedCount = 0;
let toastTimer = null;

/* ---------- helpers ---------- */

function showToast(message, kind) {
  toast.textContent = message;
  toast.classList.toggle("ok", kind === "ok");
  toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add("hidden"), 4000);
}

async function postJson(url, data) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {}),
  });
  const payload = await r.json().catch(() => ({}));
  if (!r.ok || payload.ok === false) {
    throw new Error(payload.error || `Request failed (${r.status})`);
  }
  return payload;
}

function fmtClock(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function fmtTime(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fmtDate(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function setPill(kind, text) {
  readerPill.className = `pill ${kind}`;
  readerPill.querySelector(".pill-text").textContent = text;
}

/* ---------- rendering ---------- */

function renderPill() {
  const r = state.reader;
  if (r.mock) {
    setPill("ok", "Mock reader");
  } else if (r.status === "reconnecting") {
    setPill("warn", "Reader reconnecting…");
  } else if (r.status === "error") {
    setPill("bad", "Reader error");
  } else if (r.detected) {
    setPill("ok", "Reader connected");
  } else {
    setPill("bad", "Reader not found");
  }
}

function renderHome() {
  homeClock.textContent = new Date().toLocaleString([], {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
  startBtn.disabled = !state.reader.detected;
  const last = state.last_session;
  exportBtn.disabled = !last;
  if (last) {
    const label = last.name || fmtDate(last.started_at);
    exportCaption.textContent = `Last session: ${label} · ${last.attendee_count} scanned`;
  } else {
    exportCaption.textContent = "No sessions yet. Start one to enable export.";
  }
}

function buildRow(tag, index, isNew) {
  const li = document.createElement("li");
  li.className = "tag-row" + (isNew ? " new" : "");

  const num = document.createElement("span");
  num.className = "row-num";
  num.textContent = String(index);

  const id = document.createElement("span");
  id.className = "row-id";
  if (tag.label) {
    const label = document.createElement("span");
    label.className = "row-label";
    label.textContent = tag.label;
    const epc = document.createElement("span");
    epc.className = "row-epc mono dim";
    epc.textContent = tag.epc;
    id.append(label, epc);
  } else {
    const epc = document.createElement("span");
    epc.className = "row-epc mono";
    epc.textContent = tag.epc;
    id.append(epc);
  }

  const time = document.createElement("span");
  time.className = "row-time";
  time.textContent = fmtTime(tag.scanned_at);

  li.append(num, id, time);
  return li;
}

function renderTags() {
  if (state.session_id !== renderedSession) {
    renderedSession = state.session_id;
    renderedCount = 0;
    tagList.innerHTML = "";
  }
  const tags = state.tags || [];
  const firstRender = renderedCount === 0;
  for (let i = renderedCount; i < tags.length; i++) {
    // newest on top; skip the flash when (re)painting a whole existing session
    tagList.prepend(buildRow(tags[i], i + 1, !firstRender));
  }
  renderedCount = tags.length;
  emptyHint.classList.toggle("hidden", tags.length > 0);
}

function renderLive() {
  const paused = state.status === "paused";
  liveCount.textContent = String(state.scanned_count);
  pauseBtn.textContent = paused ? "Resume" : "Pause";
  pausedBanner.classList.toggle("hidden", !paused);
  screenLive.classList.toggle("paused", paused);
  if (state.started_at) {
    liveTimer.textContent = fmtClock((Date.now() - new Date(state.started_at)) / 1000);
  }
  renderTags();
}

function render() {
  if (!state) return;
  const live = state.status === "running" || state.status === "paused";
  screenLive.classList.toggle("active", live);
  screenHome.classList.toggle("active", !live);
  renderPill();
  if (live) renderLive();
  else renderHome();
  if (!settingsDrawer.classList.contains("hidden")) {
    debugLog.textContent = (state.debug_lines || []).join("\n");
  }
}

async function poll() {
  try {
    const r = await fetch("/api/state");
    if (!r.ok) throw new Error(`state ${r.status}`);
    state = await r.json();
    render();
  } catch {
    setPill("bad", "App not responding");
  }
}

/* ---------- actions ---------- */

startBtn.addEventListener("click", async () => {
  startBtn.disabled = true;
  try {
    await postJson("/api/start", {
      uri: overrides.uri,
      antenna: overrides.antenna,
      read_power: overrides.read_power,
      session_name: overrides.session_name,
    });
    await poll();
  } catch (e) {
    showToast(e.message);
    startBtn.disabled = false;
  }
});

pauseBtn.addEventListener("click", async () => {
  try {
    if (state && state.status === "paused") {
      await postJson("/api/start", {});
    } else {
      await postJson("/api/pause", {});
    }
    await poll();
  } catch (e) {
    showToast(e.message);
  }
});

stopBtn.addEventListener("click", () => stopConfirm.classList.remove("hidden"));
$("stopCancel").addEventListener("click", () => stopConfirm.classList.add("hidden"));

$("stopConfirmBtn").addEventListener("click", async () => {
  stopConfirm.classList.add("hidden");
  try {
    await postJson("/api/stop", {});
    await poll();
  } catch (e) {
    showToast(e.message);
  }
});

exportBtn.addEventListener("click", async () => {
  exportBtn.disabled = true;
  try {
    const p = await postJson("/api/export", {});
    const s = p.session || {};
    const parts = [`${s.attendee_count ?? 0} scanned`];
    if (s.duration_text) parts.push(`session time ${s.duration_text}`);
    $("exportMeta").textContent = parts.join(" · ");
    $("exportFile").textContent = p.filename;
    $("exportUrl").textContent = p.exports_page_url;
    const qrBox = $("qrBox");
    qrBox.innerHTML = "";
    if (window.qrcode) {
      try {
        const qr = window.qrcode(0, "M");
        qr.addData(p.exports_page_url);
        qr.make();
        qrBox.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 0 });
      } catch {
        /* URL too long for auto type — panel still shows the address in text */
      }
    }
    exportPanel.classList.remove("hidden");
  } catch (e) {
    showToast(e.message);
  } finally {
    exportBtn.disabled = false;
    render();
  }
});

$("exportClose").addEventListener("click", () => exportPanel.classList.add("hidden"));

/* ---------- settings ---------- */

$("settingsLink").addEventListener("click", () => {
  if (state) {
    $("cfgUri").value = overrides.uri || state.config.uri;
    $("cfgAntenna").value = overrides.antenna || state.config.antenna;
    $("cfgPower").value = overrides.read_power || state.config.read_power;
    $("cfgSessionName").value = overrides.session_name;
    debugLog.textContent = (state.debug_lines || []).join("\n");
  }
  settingsDrawer.classList.remove("hidden");
});

$("settingsClose").addEventListener("click", () => settingsDrawer.classList.add("hidden"));

$("settingsApply").addEventListener("click", () => {
  overrides.uri = $("cfgUri").value.trim();
  overrides.antenna = $("cfgAntenna").value.trim();
  overrides.read_power = $("cfgPower").value.trim();
  overrides.session_name = $("cfgSessionName").value.trim();
  settingsDrawer.classList.add("hidden");
  showToast("Settings will apply to the next session.", "ok");
});

settingsDrawer.addEventListener("click", (e) => {
  if (e.target === settingsDrawer) settingsDrawer.classList.add("hidden");
});

/* Destructive buttons need two taps within 3 s to avoid accidents. */
function armable(btn, armedLabel, action) {
  const idleLabel = btn.textContent;
  let armed = false;
  let timer = null;
  const disarm = () => {
    armed = false;
    btn.textContent = idleLabel;
    btn.classList.remove("armed");
  };
  btn.addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      btn.textContent = armedLabel;
      btn.classList.add("armed");
      clearTimeout(timer);
      timer = setTimeout(disarm, 3000);
      return;
    }
    clearTimeout(timer);
    disarm();
    await action();
  });
}

armable($("exitBtn"), "Tap again to exit", async () => {
  try {
    await postJson("/api/exit", {});
    window.close(); // fallback for non-kiosk browsers during development
  } catch (e) {
    showToast(e.message);
  }
});

armable($("clearBtn"), "Tap again to clear", async () => {
  try {
    const p = await postJson("/api/clear-history", {});
    showToast(`Cleared ${p.removed_sessions} session(s) and all CSV exports.`, "ok");
    renderedSession = "__cleared__"; // force the tag list to repaint next poll
    await poll();
  } catch (e) {
    showToast(e.message);
  }
});

/* ---------- boot ---------- */

poll();
setInterval(poll, 1000);
