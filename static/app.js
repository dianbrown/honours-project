const statusLine = document.getElementById("statusLine");
const countsLine = document.getElementById("countsLine");
const exportLine = document.getElementById("exportLine");
const tagBody = document.getElementById("tagBody");
const logBox = document.getElementById("logBox");

const uriEl = document.getElementById("uri");
const antennaEl = document.getElementById("antenna");
const readPowerEl = document.getElementById("readPower");
const emailInput = document.getElementById("emailInput");

const startBtn = document.getElementById("startBtn");
const pauseBtn = document.getElementById("pauseBtn");
const resumeBtn = document.getElementById("resumeBtn");
const stopBtn = document.getElementById("stopBtn");
const exportBtn = document.getElementById("exportBtn");
const emailBtn = document.getElementById("emailBtn");

let lastExportFile = "";

function postJson(url, data) {
  return fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(data || {})
  }).then(async (r) => {
    const payload = await r.json().catch(() => ({}));
    if (!r.ok || payload.ok === false) {
      throw new Error(payload.error || `Request failed: ${r.status}`);
    }
    return payload;
  });
}

function renderTags(tags) {
  tagBody.innerHTML = "";
  for (const t of tags.slice().reverse()) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${t.host_timestamp || ""}</td>
      <td>${t.epc || ""}</td>
      <td>${t.antenna || ""}</td>
      <td>${t.read_count || ""}</td>
      <td>${t.reader_timestamp || ""}</td>
    `;
    tagBody.appendChild(tr);
  }
}

function applyState(state) {
  statusLine.textContent = `Status: ${state.status}`;
  countsLine.textContent = `Total reads: ${state.tag_count} | Unique tags: ${state.unique_tag_count}`;

  renderTags(state.tags || []);
  for (const line of (state.new_logs || [])) {
    logBox.textContent += `${line}\n`;
  }
  logBox.scrollTop = logBox.scrollHeight;

  lastExportFile = state.last_export_file || "";
  if (lastExportFile) {
    exportLine.innerHTML = `Latest export: <a href="/downloads/${lastExportFile}" target="_blank">${lastExportFile}</a>`;
  }

  const running = state.status === "running";
  const paused = state.status === "paused";
  const stopped = state.status === "stopped";

  startBtn.disabled = running;
  pauseBtn.disabled = !running;
  resumeBtn.disabled = !paused;
  stopBtn.disabled = !(running || paused);
  exportBtn.disabled = !stopped;
}

async function refreshState() {
  const r = await fetch("/api/state");
  const state = await r.json();
  applyState(state);
}

startBtn.addEventListener("click", async () => {
  try {
    await postJson("/api/start", {
      uri: uriEl.value.trim(),
      antenna: Number(antennaEl.value),
      read_power: Number(readPowerEl.value),
    });
    await refreshState();
  } catch (e) { alert(e.message); }
});

pauseBtn.addEventListener("click", async () => {
  try {
    await postJson("/api/pause");
    await refreshState();
  } catch (e) { alert(e.message); }
});

resumeBtn.addEventListener("click", async () => {
  try {
    await postJson("/api/start", {
      uri: uriEl.value.trim(),
      antenna: Number(antennaEl.value),
      read_power: Number(readPowerEl.value),
    });
    await refreshState();
  } catch (e) { alert(e.message); }
});

stopBtn.addEventListener("click", async () => {
  try {
    await postJson("/api/stop");
    await refreshState();
  } catch (e) { alert(e.message); }
});

exportBtn.addEventListener("click", async () => {
  try {
    const p = await postJson("/api/export");
    lastExportFile = p.filename;
    exportLine.innerHTML = `Latest export: <a href="${p.download_url}" target="_blank">${p.filename}</a>`;
    await refreshState();
  } catch (e) { alert(e.message); }
});

emailBtn.addEventListener("click", async () => {
  const to = emailInput.value.trim();
  if (!to) {
    alert("Enter recipient email.");
    return;
  }
  try {
    await postJson("/api/email", {to_email: to, filename: lastExportFile});
    alert("Email sent.");
  } catch (e) { alert(e.message); }
});

refreshState();
setInterval(refreshState, 1000);
