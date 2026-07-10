# Implementation Plan — UHF RFID Attendance System (Raspberry Pi 5 Kiosk)

Honours project: an attendance-taking kiosk built from a **SparkFun M7E Hecto** UHF RFID module
(+ UHF antenna) connected to a **Raspberry Pi 5**, with a **Raspberry Pi Touch Display 2** as the
lecturer-facing interface. The lecturer starts / pauses / stops an attendance session on the touch
screen, watches tags appear live, and afterwards downloads the results as CSV by connecting their
phone/laptop directly to a Wi-Fi hotspot hosted by the Pi (no campus Wi-Fi needed).

---

## 1. Current state of the repository

| Component | File(s) | Status |
|---|---|---|
| Mercury API C SDK (vendored) | `c/` | Complete SDK from ThingMagic, incl. `readasync.c` sample that prints `Background read: Tag ID:<EPC> ant:<n> count:<n> time:<ts>` lines and accepts `--ant` / `--pow` args |
| Reader bridge | `attendance_kiosk.py` → `ReadAsyncProcess` | Works: spawns the compiled `readasync` binary, parses stdout lines with a regex, forwards tag events |
| Session logic | `attendance_kiosk.py` → `AttendanceController`, `SessionState` | Works: `idle → running → paused → stopped` state machine; pause kills the reader process, resume restarts it. **In-memory only** — a crash or reboot loses the session |
| Web API | Flask routes `/api/state`, `/api/start`, `/api/pause`, `/api/stop`, `/api/export`, `/api/email`, `/downloads/<file>` | Works, 1 s polling from the browser |
| UI | `templates/index.html`, `static/app.js`, `static/style.css` | Single admin-style page: config inputs, five buttons, raw-read table, log box. **Not** a kiosk touch UI |
| CSV export | `AttendanceController.export_csv()` → `exports/` | Works: dumps every raw read (one row per read, duplicates included) |
| Email export | `send_email()` (SMTP env vars) | **To be removed** — not needed; export happens via the hotspot download page |
| Launch scripts | `run-attendance-kiosk.{sh,ps1,cmd}`, `run-hecto-live.{sh,ps1,cmd}` | Build `readasync` (`make TMR_ENABLE_UHF=1 TMR_ENABLE_SERIAL_READER_ONLY=1`) then start the app |
| Docs | `KIOSK_SETUP.md` | Manual setup notes incl. Chromium kiosk autostart idea |

### Gaps to close

1. **Touch UI** — needs to be a full-screen, screen-based kiosk flow (Home ⇄ Live) with
   large touch targets sized for the Touch Display 2, not a form-style admin page.
2. **Attendance semantics** — a session should present **unique tags** (one row per student, with
   first-seen time), not a stream of hundreds of raw reads of the same card.
3. **Persistence** — sessions must survive an app crash / power loss and be browsable afterwards.
4. **Offline export** — Pi must run its **own Wi-Fi access point**; lecturer joins it and downloads
   the CSV from a simple downloads page (QR code shown on the kiosk summary screen).
5. **Reliability** — auto-detect the serial port, auto-restart the reader if it dies mid-session,
   graceful handling of unplugged module.
6. **Deployment** — the app and Chromium kiosk must start automatically on boot via systemd; one
   `install.sh` sets everything up on a fresh Pi.
7. **Development off-Pi** — a mock reader mode so the UI can be developed/tested on Windows without
   the hardware.

---

## 2. Target architecture

```
┌────────────────────────────── Raspberry Pi 5 ──────────────────────────────┐
│                                                                            │
│  M7E Hecto ──USB serial──► readasync (C, Mercury API)                      │
│  + UHF antenna              │ stdout: "Background read: Tag ID:…"          │
│                             ▼                                              │
│                  attendance_kiosk.py (Flask, port 8080)                    │
│                   ├─ ReadAsyncProcess   (subprocess supervisor)            │
│                   ├─ AttendanceController (state machine + dedupe)         │
│                   ├─ SQLite store  data/attendance.db                      │
│                   └─ CSV exports   exports/*.csv                           │
│                             ▲                        ▲                     │
│            localhost:8080   │                        │  10.42.0.1:8080     │
│  ┌──────────────────────────┴───────┐   ┌───────────┴──────────────────┐  │
│  │ Touch Display 2                  │   │ Wi-Fi hotspot (NetworkManager)│  │
│  │ Chromium --kiosk (lecturer UI)   │   │ SSID: AttendancePi            │  │
│  └──────────────────────────────────┘   │ Lecturer phone → /exports     │  │
│                                         └──────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────┘
```

Key decision: **keep the `readasync` subprocess bridge** rather than binding the Mercury API into
Python. It already works, the C SDK is vendored and builds on aarch64 with the existing make flags,
and the stdout contract is stable (`readasync.c:684`). We harden the bridge instead of replacing it.

Second key decision: **two UI surfaces, one Flask app**:
- `/` — the kiosk UI (touch screen, full screen, screen-switching SPA).
- `/exports` — a phone-friendly download page for lecturers over the hotspot.

---

## 3. Phase 1 — Reader bridge hardening

**Files: `attendance_kiosk.py`**

1. **Serial-port auto-detection.** Before spawning `readasync`, if the configured URI's device
   doesn't exist, probe `/dev/ttyUSB0`, `/dev/ttyACM0`, `/dev/ttyUSB1`, `/dev/ttyACM1` (same order
   as `run-hecto-live.sh`) and use the first present. Surface the resolved port in `/api/state`
   so the UI can show "Reader: /dev/ttyUSB0" or "Reader not found".
2. **Supervision / auto-restart.** Today `_on_reader_exit` just flips the session to `stopped`.
   Change: if the session is `running` and the process exits unexpectedly, mark reader state
   `reconnecting`, retry `start()` with exponential backoff (1 s, 2 s, 4 s… capped at 15 s), and
   keep the session `running` the whole time. Only give up (status → `error`) after ~2 minutes of
   failures. Add a `reader_status` field (`ok | reconnecting | error | off`) to the snapshot.
3. **Startup validation.** On app boot, verify the `readasync` binary exists and is executable;
   if missing, put a clear message in the state ("readasync not built — run install.sh") instead
   of failing on first Start.
4. **Mock mode for development.** `--mock` flag (or `ATTENDANCE_MOCK=1`): replace
   `ReadAsyncProcess` with a `MockReadProcess` that emits a configurable set of fake EPCs at random
   intervals on a background thread, using the same `on_tag` callback shape. This is how all UI
   work in later phases gets tested on Windows.
5. **udev rule (documented in install, Phase 6).** `/etc/udev/rules.d/99-hecto.rules` creating a
   stable `/dev/hecto` symlink for the module's USB-serial chip so the URI never changes between
   boots. Default URI becomes `tmr:///dev/hecto` with fallback to auto-detection.

**Acceptance:** unplugging the USB cable mid-session and re-plugging within ~30 s resumes reads
without the lecturer touching anything; the UI shows the reconnecting state meanwhile.

---

## 4. Phase 2 — Attendance model + persistence (SQLite)

**Files: `attendance_kiosk.py` (new module `storage.py` optional), new `data/` dir (gitignored)**

1. **Scan-once semantics (core requirement).** A tag is recorded the **first** time it is seen in
   a session and then ignored for the rest of that session. The tag callback keeps an in-memory
   `seen: set[epc]`; a new EPC is appended to the attendance list as `{epc, scanned_at}` and pushed
   to the UI, any repeat read of the same EPC is silently dropped. Exactly one row per tag —
   in the live list, in the database, and in the CSV. (A raw per-read debug log can be switched on
   with `ATTENDANCE_LOG_RAW=1` for the project write-up, but it is off by default.)
2. **SQLite persistence** at `data/attendance.db` (stdlib `sqlite3`, WAL mode):
   - `sessions(id TEXT PK, name TEXT, started_at, ended_at, status, antenna, read_power, uri)`
   - `attendees(session_id, epc, scanned_at, PRIMARY KEY(session_id, epc))`
   - With scan-once semantics writes are tiny (one INSERT per unique tag), so no batching is
     needed and the SD card is barely touched.
3. **Crash recovery.** On startup, any session left in `running`/`paused` is marked
   `stopped` with `ended_at = now` (data already persisted is kept). The UI's Home screen lists
   recent sessions so the lecturer can still export one after a power cut.
4. **Session naming.** `POST /api/start` accepts an optional `session_name` (e.g. "CSC311 Tue
   09:00"); used in the CSV filename: `attendance_<name>_<YYYYmmdd_HHMMSS>.csv`.
5. **CSV export:** one row per tag, in scan order —
   `session_id, session_name, epc, scanned_at` (plus `student_number, name` columns when the
   optional roster is present).
6. **New/changed endpoints:**
   - `GET /api/sessions` — list stored sessions with counts.
   - `POST /api/export` — gains `{session_id?}`; defaults to current/most recent session.
   - **Remove `/api/email`** and the whole `send_email()` / SMTP code path — email export is
     dropped (offline hotspot download is the one export mechanism).
   - Keep `/api/state`, `/api/start`, `/api/pause`, `/api/stop` shapes backward-compatible.
7. **Optional (stretch): student roster.** `data/roster.csv` (`epc,student_number,name`) loaded at
   startup if present; when an EPC matches, live UI and CSV show the student name/number alongside
   the EPC. Unknown EPCs display as "Unregistered card". This is a big demo win but the system must
   work without it.

**Acceptance:** start a session, wave 3 cards past the antenna repeatedly — the live list and the
exported CSV contain exactly 3 rows. Pull the Pi's power mid-session, boot again — the session
appears under recent sessions with its 3 attendees and exports correctly.

---

## 5. Phase 3 — Kiosk touch UI (Touch Display 2)

**Files: `templates/index.html`, `static/app.js`, `static/style.css` (rewrite all three);
vendor a tiny QR generator, e.g. `static/qrcode.min.js` (no CDN — the Pi is offline)**

Touch Display 2 is 720 × 1280 (used landscape: 1280 × 720). Design for exactly that viewport:
full-screen, no browser chrome (Chromium `--kiosk`), min 64 px touch targets, no hover-dependent
affordances, `user-select: none`, large readable type.

**Design language (explicit, to keep it clean):** flat and restrained — solid colours only,
**no gradients**, no glassmorphism/glow/drop-shadow stacks, no emoji-laden buttons. A neutral
background (near-white or near-black, pick one and stick to it), one accent colour for the
primary action, red reserved exclusively for Stop, and grey for disabled states. System font
stack, an 8 px spacing grid, 1 px hairline borders or subtle surface-tone changes to separate
regions, and at most one micro-animation (the new-row highlight). Every control has obvious
states: default / pressed / disabled. Disabled means visibly greyed out with reduced contrast —
never hidden. Motion and decoration never compete with the two things that matter: the buttons
and the scan list.

Single HTML page, two **screens** toggled by JS (`.screen.active`), driven by the same 1 s
`/api/state` polling that exists today:

1. **HOME screen**
   - Slim header: app name (left), reader status pill (right — `Reader connected` / red
     `Reader not found`).
   - A vertical stack of two full-width buttons:
     - **Start** (top, accent colour) — starts a session and switches to the Live screen.
       Disabled (greyed) while the reader is not detected.
     - **Export** (below, secondary style) — **greyed out until at least one finished session
       exists**. When enabled, tapping it exports the most recent session's CSV and opens the
       export panel: filename + "Connect to Wi-Fi **AttendancePi** → open
       **http://10.42.0.1:8080/exports**" + a **QR code** for that URL (generated client-side
       with the vendored qrcode lib). A small caption under the button shows what would be
       exported ("Last session: today 09:03 — 42 scanned") so its state is self-explanatory.
   - Tiny **Settings** text link in the footer (slide-over with URI/antenna/power — lecturers
     never need it; defaults are baked in).
2. **LIVE screen** (auto-shown when status becomes `running` or `paused`)
   - **Top bar** contains the session controls, always visible:
     **Pause** (secondary; becomes **Resume** when paused) and **Stop** (red, with a confirm tap:
     "Stop session? — Stop / Keep going"), plus the elapsed timer and a live scanned-count.
   - Below, the live list fills the rest of the screen: one row per scanned tag, newest first —
     EPC (or roster name), time scanned. **Each ID appears exactly once**; repeat reads change
     nothing. A brief single-flash highlight on a new row gives the student visual confirmation.
   - When paused: list dims and a "PAUSED — scans are not being recorded" banner shows.
   - On Stop: return to HOME, where Export is now enabled and its caption points at the session
     that just finished.

Also in this phase:
- `GET /exports` — new Flask route + `templates/exports.html`: mobile-friendly list of all exported
  CSVs (name, session, size, date) with download links, plus an "export this session" button per
  stored session. This is the page lecturers open from their phone. Same flat design language.
- Remove the email card/JS entirely (matches the `/api/email` removal in Phase 2).
- Error surfacing: replace `alert()` with an on-screen toast (alerts are ugly and can trap focus in
  kiosk Chromium).

**Acceptance (in mock mode on the dev machine, window sized 1280×720):** Home shows Export greyed
out on first run → Start → Live rows appear as mock tags arrive, each ID once → Pause/Resume →
Stop confirm → back on Home, Export now enabled → Export opens the panel with QR code. All buttons
operable with a fat-finger-sized cursor; nothing scrolls except the scan list.

---

## 6. Phase 4 — Offline export: Pi Wi-Fi hotspot

**No code changes — configuration done by `install.sh` (Phase 6). Raspberry Pi OS Bookworm uses
NetworkManager, so:**

```bash
sudo nmcli device wifi hotspot ifname wlan0 con-name attendance-hotspot \
     ssid AttendancePi password "ChooseAStrongPass"
sudo nmcli connection modify attendance-hotspot connection.autoconnect yes \
     connection.autoconnect-priority 100
```

- NetworkManager's shared mode gives the Pi **10.42.0.1** and runs DHCP for clients — no dnsmasq
  or hostapd setup needed.
- The kiosk export panel and `/exports` page hardcode/derive `http://10.42.0.1:8080/exports`
  (also reachable as `http://raspberrypi.local:8080` via mDNS for clients that support it; show the
  IP form since Android often lacks mDNS).
- While the hotspot is active the Pi has **no internet** (single Wi-Fi radio). That's fine — the
  download page on the Pi is the only export path. To give the Pi internet temporarily (e.g. for
  `git pull`), run `nmcli connection down attendance-hotspot` and join normal Wi-Fi, or plug in
  Ethernet alongside the hotspot.
- Flask already binds `0.0.0.0:8080`, so it serves the hotspot interface as-is. Keep the app
  HTTP-only and LAN-only; the download page is read-only (CSV listing + `send_from_directory`,
  which already guards against path traversal).

**Acceptance:** with no other network configured, boot the Pi, join `AttendancePi` from a phone,
open `http://10.42.0.1:8080/exports`, download a CSV, open it in Excel/Sheets.

---

## 7. Phase 5 — Pi packaging: click-to-run app, then boot-to-kiosk

**New files: `deploy/attendance-kiosk.desktop`, `deploy/attendance-kiosk.service`,
`deploy/99-hecto.rules`, `install.sh`, `enable-autostart.sh`**

This ships in two stages matching how you want to use it:

**Stage A — a normal app you click to run.** `install.sh` puts an **"Attendance Kiosk"** icon on
the Pi desktop / app menu (`deploy/attendance-kiosk.desktop`). Clicking it runs a small launcher
script that starts the Flask server if it isn't already running, waits until it answers, then
opens Chromium in `--kiosk` full-screen on `http://localhost:8080`. Closing the browser
(long-press / Alt+F4) leaves the desktop usable as normal. No systemd involved yet.

**Stage B — boot straight into the kiosk (enable later, one command).** When you're ready, run
`./enable-autostart.sh`; it installs the systemd service below and the browser autostart entry so
every boot lands directly on the Home screen with the Start button. `./enable-autostart.sh --off`
reverts to Stage A.

1. **App service** — `/etc/systemd/system/attendance-kiosk.service`:
   ```ini
   [Unit]
   Description=Attendance kiosk Flask app
   After=network.target

   [Service]
   User=pi
   WorkingDirectory=/home/pi/attendanceTaker
   ExecStart=/home/pi/attendanceTaker/.venv/bin/python attendance_kiosk.py --uri tmr:///dev/hecto
   Restart=always
   RestartSec=3

   [Install]
   WantedBy=multi-user.target
   ```
2. **Browser kiosk** — Pi OS Bookworm desktop runs **labwc** (Wayland). Autostart Chromium via
   `~/.config/labwc/autostart`:
   ```
   chromium-browser --kiosk --noerrdialogs --disable-infobars \
     --check-for-update-interval=31536000 --ozone-platform=wayland \
     http://localhost:8080 &
   ```
   plus screen-blanking off (`raspi-config nonint do_blanking 1`) and on-screen keyboard enabled
   (squeekboard, shipped with Bookworm). A small wait-for-server loop (`until curl -s
   localhost:8080 >/dev/null; do sleep 1; done`) before launching Chromium avoids a white
   error page on slow boots.
3. **Python env** — switch from `pip install` into system Python (blocked on Bookworm by PEP 668)
   to a venv: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`. Update
   `run-attendance-kiosk.sh` accordingly.
4. **`install.sh`** (idempotent, run once on a fresh Pi) does, in order:
   - `apt install` build deps (`build-essential`, `python3-venv`) + chromium if missing;
   - build `readasync`: `make -C c/src/api TMR_ENABLE_UHF=1 TMR_ENABLE_SERIAL_READER_ONLY=1 readasync`;
   - create venv + install requirements;
   - install the udev rule (`/dev/hecto`), add user to `dialout` group;
   - create the NetworkManager hotspot (Phase 4 commands, SSID/password prompted or flags);
   - install the desktop icon (Stage A) and disable screen blanking;
   - print a final checklist. Autostart (Stage B) is **not** enabled here — that stays an explicit
     `./enable-autostart.sh` later.
5. **Update `KIOSK_SETUP.md`** to describe the new one-command install and the lecturer-facing
   workflow (with the hotspot download instructions), replacing the manual steps it currently has.

**Acceptance (Stage A):** on a clean Pi OS 64-bit install: clone, `./install.sh`, double-click the
desktop icon → full-screen Home screen appears and a session works end to end.
**Acceptance (Stage B):** after `./enable-autostart.sh` + reboot, the touch screen boots straight
into the Home screen with no keyboard/mouse attached.

---

## 8. Phase 6 — Testing & validation

1. **Unit tests (`tests/`, pytest, runnable on Windows):**
   - stdout regex parsing (`TAG_LINE_RE`) against captured real `readasync` output;
   - state-machine transitions incl. illegal ones (start while running, pause while idle…);
   - scan-once logic (same EPC read many times → exactly one row, `scanned_at` = first read);
   - SQLite round-trip + crash-recovery path;
   - CSV content and column order.
2. **Mock-mode UI test:** manual script in `TESTING.md` covering the Phase 3 acceptance flow.
3. **On-Pi hardware checklist** (also in `TESTING.md`):
   - module detected (`/dev/hecto` exists), `readasync` reads a tag from the CLI first
     (`./run-hecto-live.sh`);
   - antenna/read-power sanity: verify read range with the actual UHF antenna, tune
     `ATTENDANCE_READ_POWER` (start 1900 cdBm ≈ 19 dBm; M7E max is 27 dBm — higher power = more
     range but more USB current draw: keep the Pi 5 on the official 27 W PSU and set
     `usb_max_current_enable=1` in `/boot/firmware/config.txt` if reads become unstable at
     high power);
   - region check: `readasync` auto-selects the module's first supported region if none set
     (`readasync.c:418-439`) — confirm it matches your locale's legal band and hard-set it if not;
   - 45-minute soak test: run a session for a full lecture length, confirm no memory growth, no
     reader drop, DB and CSV consistent;
   - unplug/replug USB mid-session (Phase 1 acceptance);
   - power-cut recovery (Phase 2 acceptance);
   - hotspot download from Android + iPhone + laptop (Phase 4 acceptance).

---

## 9. Installing on the Pi — step by step (the painless path)

Development happens entirely on this Windows PC using mock mode (`--mock`), so the Pi only ever
receives finished code. **Regular Raspberry Pi OS is exactly what this targets** — Python 3,
Flask, `gcc/make` for `readasync`, Chromium and NetworkManager are all standard on the stock
desktop image; nothing exotic gets installed. (If the Pi turns out to be on the older Bullseye
release rather than Bookworm, `install.sh` detects it and adjusts the two things that differ:
the browser autostart mechanism and the hotspot setup.)

**One-time install (~10 minutes, Pi needs internet once):**

```bash
# on the Pi, in a terminal:
sudo apt update
git clone <your repo URL> attendanceTaker
cd attendanceTaker
./install.sh
```

`install.sh` builds the reader binary, creates the Python venv, installs the udev rule, sets up
the hotspot, and puts the **Attendance Kiosk** icon on the desktop. If the Pi can't get internet,
clone the repo onto a USB stick on Windows and copy it across instead — `install.sh` then only
needs the apt packages, which ship on the stock image except `build-essential` (usually present).

**Day-to-day use:** plug in the M7E Hecto, double-click the **Attendance Kiosk** icon → tap
**Start** → students scan (each ID appears once) → tap **Stop** → **Export** → lecturer joins the
`AttendancePi` Wi-Fi and downloads the CSV via the QR code on screen.

**Updating after development on Windows:** push from the PC, then on the Pi
`git pull && ./install.sh` (idempotent — it only redoes what changed).

**When you're ready for boot-to-kiosk:** `./enable-autostart.sh`, reboot once. From then on the
Pi powers straight into the Home screen. `./enable-autostart.sh --off` undoes it.

## 10. Suggested execution order & effort

| # | Phase | Depends on | Effort |
|---|---|---|---|
| 1 | Reader bridge hardening + mock mode | — | 0.5–1 day |
| 2 | Attendance model + SQLite | 1 (mock mode for tests) | 1 day |
| 3 | Kiosk touch UI + `/exports` page | 2 (new state shape) | 1.5–2 days |
| 4 | Wi-Fi hotspot | — (config only) | 0.5 day |
| 5 | systemd deployment + `install.sh` | 1–4 | 0.5–1 day |
| 6 | Testing on hardware | all | 1 day + soak |

Phases 1–3 are fully developable and testable on the Windows dev machine using mock mode;
phases 4–6 need the Pi.

## 11. File change summary

```
attendance_kiosk.py        modify   port autodetect, supervision, mock mode, scan-once
                                    logic, SQLite, session naming, /api/sessions, /exports;
                                    remove /api/email + SMTP code
storage.py                 new      (optional split) SQLite layer
templates/index.html       rewrite  3-screen kiosk SPA
templates/exports.html     new      phone-facing download page
static/app.js              rewrite  screen router + state rendering + toasts
static/style.css           rewrite  1280×720 kiosk theme, large touch targets
static/qrcode.min.js       new      vendored QR generator (offline)
deploy/attendance-kiosk.desktop  new  click-to-run desktop icon (Stage A)
deploy/attendance-kiosk.service  new  systemd unit (Stage B)
deploy/99-hecto.rules      new      stable /dev/hecto symlink
install.sh                 new      one-shot Pi provisioning
enable-autostart.sh        new      toggle boot-to-kiosk on/off
run-attendance-kiosk.sh    modify   use .venv
tests/…                    new      pytest suite
TESTING.md                 new      manual + hardware checklists
KIOSK_SETUP.md             rewrite  reflect install.sh + hotspot workflow
data/, exports/            runtime  gitignore db + csv outputs
```

## 12. Risks / open questions

- **Serial chip & port name.** SparkFun's M7E Hecto breakout uses a CH340 USB-serial
  (→ `/dev/ttyUSB0` on Pi OS, driver in-kernel). The udev rule + auto-detection covers either
  ttyUSB/ttyACM enumeration. Verify once on real hardware.
- **Read power vs. USB power budget.** At high read power the module can draw close to the USB
  budget; if the module brownouts (random disconnects), lower `--pow` or power the module
  appropriately. The soak test catches this.
- **Region compliance.** Confirm the auto-selected region matches your country's UHF band before
  the demo; set it explicitly in `readasync` args/config if needed.
- **Tag-to-student mapping.** The core system records EPCs. The roster feature (Phase 2 stretch)
  is what turns the demo from "tag IDs on a screen" into "student names on a screen" — decide
  early whether student cards' EPCs can be collected for a roster CSV.
- **Multiple students at once.** UHF anti-collision handles simultaneous tags natively; the dedupe
  layer makes bursts harmless. No extra work expected, but verify with 5+ tags in the soak test.
