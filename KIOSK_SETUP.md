# Attendance Kiosk — Raspberry Pi Setup

UHF RFID attendance system: SparkFun **M7E Hecto** + UHF antenna → **Raspberry Pi 5** →
**Touch Display 2** kiosk UI. The lecturer taps **Start**, students scan (each tag is
recorded **once**), the lecturer taps **Stop**, then **Export** — and downloads the CSV
from a phone/laptop over the Pi's own Wi-Fi hotspot. No campus network needed.

## Install on the Pi (one time, ~10 minutes)

Needs internet during install (Ethernet or Wi-Fi). In a terminal on the Pi:

```bash
sudo apt update
git clone <your repo URL> attendanceTaker
cd attendanceTaker
./install.sh
```

`install.sh` is safe to re-run (do so after every `git pull`). It:

1. installs build tools + Python venv support,
2. builds the Mercury API `readasync` reader binary,
3. creates `.venv` and installs Python dependencies,
4. installs a udev rule so the reader always appears as `/dev/hecto`, and adds you to `dialout`,
5. creates the Wi-Fi hotspot **AttendancePi** (password `attendance123` — override with
   `HOTSPOT_SSID=... HOTSPOT_PASS=... ./install.sh`),
6. puts the **Attendance Kiosk** icon on the desktop,
7. disables screen blanking.

> Installing over SSH-via-Wi-Fi? Use `SKIP_HOTSPOT=1 ./install.sh` — creating the hotspot
> switches wlan0 and would drop your connection. Create it later by re-running plain
> `./install.sh` from the Pi's own screen.

Then **log out and back in once** (dialout group), plug in the M7E Hecto over USB,
and double-click **Attendance Kiosk**.

## Day-to-day use

1. Double-click **Attendance Kiosk** → full-screen Home with a **Start** button.
2. Tap **Start** → live screen. Every scanned tag appears once, newest on top.
3. **Pause/Resume** optional; **Stop** (with confirm) ends the session.
4. Back on Home, tap **Export** → shows the download instructions + QR code.
5. On your phone: join Wi-Fi **AttendancePi** → open `http://10.42.0.1:8080/exports`
   (or scan the QR) → **Download** the session CSV.

CSV columns: `session_id, session_name, epc, scanned_at`
(+ `student_number, name` when a roster is present, see below).

## Boot straight into the kiosk (optional)

When you want the Pi to power on directly into the app:

```bash
./enable-autostart.sh      # then: sudo reboot
./enable-autostart.sh --off   # go back to the desktop icon
```

This installs a systemd service for the server (auto-restarts if it ever crashes)
plus a browser autostart entry.

## Optional: student roster

Create `data/roster.csv` to show names instead of raw tag IDs:

```csv
epc,student_number,name
E28068940000501234567890,u21000001,Alice Example
```

EPCs must be uppercase hex exactly as shown in the live list. Restart the app after editing.

## Configuration

Defaults work out of the box. Override via the on-screen **Settings** drawer (per-session)
or environment variables / flags (persistent):

| Setting | Env var | Default |
|---|---|---|
| Reader URI | `ATTENDANCE_URI` | `tmr:///dev/hecto` (auto-falls back to ttyUSB0/ttyACM0…) |
| Antenna | `ATTENDANCE_ANTENNA` | `1` |
| Read power (cdBm) | `ATTENDANCE_READ_POWER` | `1900` (M7E max 2700 — higher = more range + more current) |
| Port | `ATTENDANCE_PORT` | `8080` |
| Mock reader | `ATTENDANCE_MOCK=1` or `--mock` | off |

## Development on Windows (no hardware)

```powershell
.\run-attendance-kiosk.ps1 -Mock     # then open http://localhost:8080
python -m pytest tests\ -q           # unit tests
```

The mock reader emits repeating fake tags so the scan-once behaviour is visible.

## Troubleshooting

- **"Reader not found" on Home** — check the USB cable; `ls -l /dev/hecto` should exist.
  If your USB-serial chip isn't matched, see `deploy/99-hecto.rules` (find IDs with `lsusb`).
- **Reader drops at high read power** — the module can brown out over USB. Lower the read
  power in Settings, use the official 27 W PSU, and add `usb_max_current_enable=1` to
  `/boot/firmware/config.txt`.
- **Need internet on the Pi while the hotspot exists** — `sudo nmcli connection down
  attendance-hotspot`, connect to normal Wi-Fi (or just plug Ethernet), and
  `sudo nmcli connection up attendance-hotspot` when done.
- **Server logs** — `data/app.log` (desktop icon runs) or
  `journalctl -u attendance-kiosk -f` (autostart mode).
- **Test reads without the app** — `./run-hecto-live.sh` prints raw reads in a terminal.
