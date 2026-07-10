# Testing checklist

## Unit tests (any machine)

```bash
python -m pytest tests/ -q
```

Covers: readasync stdout parsing, scan-once dedupe, the session state machine,
SQLite persistence + crash recovery, CSV export (with and without roster).

## Mock-mode UI walkthrough (Windows or Pi, no hardware)

Run `.\run-attendance-kiosk.ps1 -Mock` (Windows) or `./run-attendance-kiosk.sh --mock` (Pi),
open http://localhost:8080 and size the window to about 1280×720:

1. Home shows **Start** enabled (pill says "Mock reader") and **Export greyed out**
   with caption "No sessions yet" on a fresh database.
2. Tap Start → Live screen. Rows appear as fake tags arrive; the counter matches the
   row count; every EPC appears exactly once; new rows flash briefly.
3. Tap Pause → banner appears, list dims, count stops increasing. Resume continues
   the same session (same rows still there).
4. Tap Stop → confirm dialog → confirm → back Home; Export now enabled with
   "Last session: … — N scanned".
5. Tap Export → panel shows filename, hotspot instructions, and a QR code. Open the
   shown /exports URL in another tab → session listed → Download → CSV has N rows.
6. Kill the server mid-session (start a session first) and restart it → Home again,
   the interrupted session appears as the last session with its tags intact.

## On-Pi hardware checklist

1. **Module detected**: plug in M7E Hecto → `ls -l /dev/hecto` exists. If not, `lsusb`
   and extend `deploy/99-hecto.rules`.
2. **Raw reads work**: `./run-hecto-live.sh` prints `Background read: Tag ID:…` lines
   when a tag is near the antenna. Sort any reader/region issue here before blaming the app.
3. **Region**: the reader auto-selects its first supported region if unset — confirm it is
   legal for your locale (see the Mercury API programmer guide) and hard-set if needed.
4. **Kiosk flow**: repeat the mock walkthrough above with real cards.
5. **Range/power tuning**: adjust read power in Settings (start 1900 cdBm, max 2700).
   Watch for USB brownouts at high power (random reader drops → see KIOSK_SETUP
   troubleshooting).
6. **Unplug/replug**: pull the USB cable mid-session → pill shows "Reader reconnecting…" →
   replug within ~60 s → reads resume, no data lost.
7. **Power cut**: pull the Pi's power mid-session → boot → session recovered as stopped
   with its scans; export works.
8. **Hotspot export**: from a phone (Android + iPhone if possible) join AttendancePi,
   open http://10.42.0.1:8080/exports, download and open the CSV.
9. **Soak test**: run one full lecture length (45–60 min) with 5+ tags; confirm reads stay
   stable, the UI stays responsive, and DB/CSV agree at the end.
10. **Multi-tag burst**: present 5+ tags simultaneously — all appear, each once.
