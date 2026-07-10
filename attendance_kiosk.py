#!/usr/bin/env python3
"""Attendance kiosk for a SparkFun M7E Hecto UHF RFID reader on a Raspberry Pi 5.

Serves three surfaces from one Flask app:
  /            kiosk touch UI (Chromium --kiosk on the Pi Touch Display 2)
  /exports     phone-friendly CSV download page (reached via the Pi's Wi-Fi hotspot)
  /api/*       session control + 1 s state polling

Tag reads come from the Mercury API `readasync` C sample run as a subprocess.
Run with --mock (or ATTENDANCE_MOCK=1) to develop without the hardware.

Attendance is scan-once: an EPC is recorded the first time it is seen in a
session and every repeat read is dropped. Sessions and attendees persist in
SQLite so a power cut never loses a session.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import io
import os
import pathlib
import random
import re
import socket
import sqlite3
import subprocess
import threading
import time
from typing import Any, Callable, Optional

from flask import Flask, Response, jsonify, render_template, request, send_from_directory
from werkzeug.exceptions import HTTPException


APP_DIR = pathlib.Path(__file__).resolve().parent

TAG_LINE_RE = re.compile(
    r"Background read: Tag ID:(?P<epc>[0-9A-F]+)\s+ant:(?P<ant>\d+)\s+count:(?P<count>\d+)\s+time:(?P<reader_time>.+)"
)

SERIAL_CANDIDATES = ["/dev/hecto", "/dev/ttyUSB0", "/dev/ttyACM0", "/dev/ttyUSB1", "/dev/ttyACM1"]

MAX_RECONNECT_ATTEMPTS = 8  # 1+2+4+8+15*4 s of backoff before giving up


def iso_now() -> str:
    return dt.datetime.now().isoformat(timespec="milliseconds")


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "session"


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def uri_to_device(uri: str) -> str:
    for prefix in ("tmr://", "llrp://"):
        if uri.startswith(prefix):
            return uri[len(prefix):]
    return uri


def device_to_uri(device: str) -> str:
    return device if device.startswith(("tmr://", "llrp://")) else f"tmr://{device}"


def find_serial_device(preferred_uri: str = "") -> tuple[Optional[str], Optional[str]]:
    """Return (uri, device_path) for the first present serial device, else (None, None)."""
    candidates = []
    if preferred_uri:
        dev = uri_to_device(preferred_uri)
        if dev:
            candidates.append(dev)
    candidates.extend(c for c in SERIAL_CANDIDATES if c not in candidates)
    for dev in candidates:
        if os.path.exists(dev):
            return device_to_uri(dev), dev
    return None, None


def _hotspot_ip() -> Optional[str]:
    """NetworkManager shared-mode hotspots put the Pi at 10.42.0.1."""
    if os.name == "nt":
        return None
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr"], capture_output=True, text=True, timeout=2
        ).stdout
        if "10.42.0.1" in out:
            return "10.42.0.1"
    except Exception:
        pass
    return None


_url_cache: dict[str, Any] = {"ts": 0.0, "url": ""}


def export_base_url(port: int) -> str:
    """Base URL lecturers should use from their phone (hotspot IP when active)."""
    now = time.monotonic()
    if not _url_cache["url"] or now - _url_cache["ts"] > 30:
        ip = os.getenv("ATTENDANCE_EXPORT_IP", "") or _hotspot_ip() or local_ip()
        _url_cache["url"] = f"http://{ip}:{port}"
        _url_cache["ts"] = now
    return _url_cache["url"]


def load_roster(path: pathlib.Path) -> dict[str, dict[str, str]]:
    """Optional data/roster.csv (epc,student_number,name) mapping EPCs to students."""
    roster: dict[str, dict[str, str]] = {}
    if not path.exists():
        return roster
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            epc = (row.get("epc") or "").strip().upper()
            if epc:
                roster[epc] = {
                    "student_number": (row.get("student_number") or "").strip(),
                    "name": (row.get("name") or "").strip(),
                }
    return roster


class Storage:
    """SQLite persistence: one row per session, one row per (session, tag)."""

    def __init__(self, db_path: pathlib.Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS sessions(
                       id TEXT PRIMARY KEY,
                       name TEXT NOT NULL DEFAULT '',
                       started_at TEXT NOT NULL,
                       ended_at TEXT NOT NULL DEFAULT '',
                       status TEXT NOT NULL,
                       uri TEXT NOT NULL DEFAULT '',
                       antenna INTEGER NOT NULL DEFAULT 1,
                       read_power INTEGER NOT NULL DEFAULT 1900)"""
            )
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS attendees(
                       session_id TEXT NOT NULL,
                       epc TEXT NOT NULL,
                       scanned_at TEXT NOT NULL,
                       PRIMARY KEY(session_id, epc))"""
            )
            self._conn.commit()

    def recover_interrupted(self) -> int:
        """Mark sessions left running/paused by a crash or power cut as stopped."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET status='stopped',"
                " ended_at=CASE WHEN ended_at='' THEN ? ELSE ended_at END"
                " WHERE status IN ('running','paused')",
                (iso_now(),),
            )
            self._conn.commit()
            return cur.rowcount

    def create_session(self, sid: str, name: str, started_at: str, uri: str, antenna: int, read_power: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(id,name,started_at,status,uri,antenna,read_power)"
                " VALUES(?,?,?,'running',?,?,?)",
                (sid, name, started_at, uri, antenna, read_power),
            )
            self._conn.commit()

    def set_session_status(self, sid: str, status: str, ended_at: str = "") -> None:
        with self._lock:
            if ended_at:
                self._conn.execute(
                    "UPDATE sessions SET status=?, ended_at=? WHERE id=?", (status, ended_at, sid)
                )
            else:
                self._conn.execute("UPDATE sessions SET status=? WHERE id=?", (status, sid))
            self._conn.commit()

    def delete_session(self, sid: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM attendees WHERE session_id=?", (sid,))
            self._conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
            self._conn.commit()

    def add_attendee(self, sid: str, epc: str, scanned_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO attendees(session_id,epc,scanned_at) VALUES(?,?,?)",
                (sid, epc, scanned_at),
            )
            self._conn.commit()

    def session(self, sid: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def attendees(self, sid: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT epc, scanned_at FROM attendees WHERE session_id=? ORDER BY scanned_at, epc",
                (sid,),
            ).fetchall()
        return [dict(r) for r in rows]

    def sessions_with_counts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.*, COUNT(a.epc) AS attendee_count"
                " FROM sessions s LEFT JOIN attendees a ON a.session_id = s.id"
                " GROUP BY s.id ORDER BY s.started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def last_finished(self) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT s.*, COUNT(a.epc) AS attendee_count"
                " FROM sessions s LEFT JOIN attendees a ON a.session_id = s.id"
                " WHERE s.status='stopped'"
                " GROUP BY s.id ORDER BY s.started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class ReadAsyncProcess:
    """Runs the Mercury API `readasync` sample and parses its stdout tag lines."""

    def __init__(
        self,
        readasync_bin: str,
        uri: str,
        antenna: int,
        read_power: int,
        on_tag: Callable[[dict[str, Any]], None],
        on_line: Callable[[str], None],
        on_exit: Callable[[int], None],
    ) -> None:
        self._readasync_bin = readasync_bin
        self._uri = uri
        self._antenna = antenna
        self._read_power = read_power
        self._on_tag = on_tag
        self._on_line = on_line
        self._on_exit = on_exit
        self._process: Optional[subprocess.Popen[str]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("Reader already started.")
        cmd = [
            self._readasync_bin,
            self._uri,
            "--ant",
            str(self._antenna),
            "--pow",
            str(self._read_power),
        ]
        env = os.environ.copy()
        exe_path = pathlib.Path(self._readasync_bin).resolve()
        path_parts = [str(exe_path.parent)]

        # Windows: ensure pthreadVC2.dll path is available for ReadAsync.exe.
        if os.name == "nt":
            pthread_dir = exe_path.parent.parent.parent / "src" / "arch" / "win32" / "lib"
            if pthread_dir.exists():
                path_parts.append(str(pthread_dir))

        existing_path = env.get("PATH", "")
        env["PATH"] = os.pathsep.join(path_parts + ([existing_path] if existing_path else []))

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        self._on_line(f"Reader process started: {' '.join(cmd)}")
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _reader_loop(self) -> None:
        assert self._process is not None
        assert self._process.stdout is not None
        process = self._process
        for raw in process.stdout:
            line = raw.strip()
            if not line:
                continue
            self._on_line(line)
            m = TAG_LINE_RE.search(line)
            if m:
                self._on_tag(
                    {
                        "epc": m.group("epc"),
                        "antenna": int(m.group("ant")),
                        "read_count": int(m.group("count")),
                        "reader_timestamp": m.group("reader_time").strip(),
                    }
                )
            if self._stop.is_set():
                break
        try:
            rc = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            rc = -1
        if not self._stop.is_set():
            self._on_line(f"Reader process exited with code {rc}")
        self._on_exit(rc)

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        self._process = None
        self._thread = None


class MockReadProcess:
    """Fake reader for development without hardware. Repeats EPCs on purpose so
    the scan-once behaviour is visible."""

    EPC_POOL = [f"E2806894000050{i:010X}" for i in range(1, 15)]

    def __init__(
        self,
        on_tag: Callable[[dict[str, Any]], None],
        on_line: Callable[[str], None],
        on_exit: Callable[[int], None],
        interval: tuple[float, float] = (0.7, 2.5),
    ) -> None:
        self._on_tag = on_tag
        self._on_line = on_line
        self._interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop.clear()
        self._on_line("Mock reader started (no hardware attached).")
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(random.uniform(*self._interval)):
            epc = random.choice(self.EPC_POOL)
            self._on_tag(
                {
                    "epc": epc,
                    "antenna": 1,
                    "read_count": 1,
                    "reader_timestamp": iso_now(),
                }
            )

    def stop(self) -> None:
        self._stop.set()
        self._thread = None


class AttendanceController:
    """Session state machine (idle → running ⇄ paused → stopped) with scan-once
    attendance, SQLite persistence, and reader supervision/auto-reconnect."""

    def __init__(
        self,
        storage: Storage,
        export_dir: pathlib.Path,
        default_uri: str,
        default_antenna: int,
        default_read_power: int,
        reader_factory: Callable[..., Any],
        mock: bool = False,
        roster: Optional[dict[str, dict[str, str]]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage = storage
        self._export_dir = export_dir
        self._default_uri = default_uri
        self._reader_factory = reader_factory
        self._mock = mock
        self._roster = roster or {}

        self._status = "idle"  # idle | running | paused | stopped
        self._session_id = ""
        self._session_name = ""
        self._started_at = ""
        self._ended_at = ""
        self._uri = default_uri
        self._antenna = default_antenna
        self._read_power = default_read_power

        self._seen: set[str] = set()
        self._tags: list[dict[str, Any]] = []
        self._reader: Optional[Any] = None
        self._reader_status = "off"  # off | ok | reconnecting | error
        self._epoch = 0
        self._reconnect_attempts = 0
        self._debug: collections.deque[str] = collections.deque(maxlen=50)
        self._last_export_file = ""

    # ----- reader callbacks -------------------------------------------------

    def _log(self, line: str) -> None:
        stamped = f"[{iso_now()}] {line}"
        self._debug.append(stamped)
        print(stamped, flush=True)

    def _on_line(self, line: str) -> None:
        with self._lock:
            self._log(line)

    def _on_tag(self, tag: dict[str, Any]) -> None:
        epc = str(tag.get("epc", "")).upper()
        if not epc:
            return
        with self._lock:
            if self._status != "running" or epc in self._seen:
                return
            self._seen.add(epc)
            self._reconnect_attempts = 0  # reads flowing again → reader is healthy
            row = {"epc": epc, "scanned_at": iso_now(), "label": self._label(epc)}
            self._tags.append(row)
            sid = self._session_id
        self._storage.add_attendee(sid, epc, row["scanned_at"])

    def _on_reader_exit(self, rc: int) -> None:
        with self._lock:
            self._reader = None
            if self._status != "running":
                self._reader_status = "off"
                return
            self._log(f"Reader exited unexpectedly (rc={rc}).")
            self._schedule_reconnect()

    # ----- reader supervision -----------------------------------------------

    def _make_reader(self) -> Any:
        return self._reader_factory(
            uri=self._uri,
            antenna=self._antenna,
            read_power=self._read_power,
            on_tag=self._on_tag,
            on_line=self._on_line,
            on_exit=self._on_reader_exit,
        )

    def _schedule_reconnect(self) -> None:
        # caller holds self._lock, session is running
        self._reconnect_attempts += 1
        if self._reconnect_attempts > MAX_RECONNECT_ATTEMPTS:
            self._reader_status = "error"
            self._log("Reader could not be recovered — session keeps its data; stop and export.")
            return
        self._reader_status = "reconnecting"
        delay = min(15.0, float(2 ** (self._reconnect_attempts - 1)))
        self._log(f"Reconnecting to reader in {delay:.0f}s (attempt {self._reconnect_attempts}).")
        threading.Thread(
            target=self._reconnect_worker, args=(self._epoch, delay), daemon=True
        ).start()

    def _reconnect_worker(self, epoch: int, delay: float) -> None:
        time.sleep(delay)
        with self._lock:
            if epoch != self._epoch or self._status != "running" or self._reader is not None:
                return
            if not self._mock:
                resolved, _dev = find_serial_device(self._uri or self._default_uri)
                if resolved is None:
                    self._log("Reader device not present yet.")
                    self._schedule_reconnect()
                    return
                self._uri = resolved
            try:
                reader = self._make_reader()
            except Exception as exc:
                self._log(f"Reader restart failed: {exc}")
                self._schedule_reconnect()
                return
            self._reader = reader
        try:
            reader.start()
            with self._lock:
                if epoch == self._epoch and self._status == "running":
                    self._reader_status = "ok"
                    self._log("Reader reconnected.")
        except Exception as exc:
            with self._lock:
                if epoch == self._epoch and self._status == "running" and self._reader is reader:
                    self._reader = None
                    self._log(f"Reader restart failed: {exc}")
                    self._schedule_reconnect()

    # ----- session control ---------------------------------------------------

    def start(
        self,
        uri: Optional[str] = None,
        antenna: Optional[int] = None,
        read_power: Optional[int] = None,
        session_name: str = "",
    ) -> None:
        with self._lock:
            if self._status == "running":
                raise RuntimeError("Session already running.")

            resuming = self._status == "paused"
            requested_uri = (uri or "").strip() or (self._uri if resuming else self._default_uri)
            if antenna is not None:
                self._antenna = int(antenna)
            if read_power is not None:
                self._read_power = int(read_power)

            if self._mock:
                self._uri = requested_uri or "mock://"
            else:
                resolved, _dev = find_serial_device(requested_uri)
                if resolved is None:
                    raise RuntimeError("RFID reader not found — check the USB connection.")
                self._uri = resolved

            new_session = not resuming
            if new_session:
                sid = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                self._session_id = sid
                self._session_name = session_name.strip()
                self._started_at = iso_now()
                self._ended_at = ""
                self._seen = set()
                self._tags = []
                self._last_export_file = ""
                self._storage.create_session(
                    sid, self._session_name, self._started_at, self._uri, self._antenna, self._read_power
                )
            else:
                self._storage.set_session_status(self._session_id, "running")

            self._status = "running"
            self._epoch += 1
            self._reconnect_attempts = 0
            sid = self._session_id

        try:
            with self._lock:
                reader = self._make_reader()
                self._reader = reader
            reader.start()
        except Exception:
            with self._lock:
                self._reader = None
                self._reader_status = "off"
                if new_session:
                    self._status = "idle"
                    self._session_id = ""
                    self._storage.delete_session(sid)
                else:
                    self._status = "paused"
                    self._storage.set_session_status(sid, "paused")
            raise
        with self._lock:
            self._reader_status = "ok"
            self._log(("Session started" if new_session else "Session resumed") + f": {sid}")

    def pause(self) -> None:
        with self._lock:
            if self._status != "running":
                raise RuntimeError("Session is not running.")
            self._status = "paused"
            self._epoch += 1
            reader = self._reader
            self._reader = None
            self._reader_status = "off"
            sid = self._session_id
            self._log("Session paused.")
        self._storage.set_session_status(sid, "paused")
        if reader is not None:
            reader.stop()

    def stop(self) -> None:
        with self._lock:
            if self._status not in ("running", "paused"):
                raise RuntimeError("Session is not running or paused.")
            self._status = "stopped"
            self._ended_at = iso_now()
            self._epoch += 1
            reader = self._reader
            self._reader = None
            self._reader_status = "off"
            sid = self._session_id
            ended = self._ended_at
            self._log(f"Session stopped: {sid} ({len(self._tags)} tags).")
        self._storage.set_session_status(sid, "stopped", ended_at=ended)
        if reader is not None:
            reader.stop()

    def shutdown(self) -> None:
        with self._lock:
            reader = self._reader
            self._reader = None
            self._epoch += 1
        if reader is not None:
            reader.stop()

    # ----- state & export -----------------------------------------------------

    def _label(self, epc: str) -> str:
        entry = self._roster.get(epc)
        if not entry:
            return ""
        name, num = entry.get("name", ""), entry.get("student_number", "")
        if name and num:
            return f"{name} ({num})"
        return name or num

    def _reader_detection(self) -> tuple[bool, str]:
        if self._mock:
            return True, "mock reader"
        _uri, dev = find_serial_device(self._uri or self._default_uri)
        return (dev is not None), (dev or "")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            detected, device = self._reader_detection()
            state = {
                "status": self._status,
                "session_id": self._session_id,
                "session_name": self._session_name,
                "started_at": self._started_at,
                "ended_at": self._ended_at,
                "scanned_count": len(self._tags),
                "tags": list(self._tags[-1000:]),
                "reader": {
                    "detected": detected,
                    "device": device,
                    "status": self._reader_status,
                    "mock": self._mock,
                },
                "config": {
                    "uri": self._uri or self._default_uri,
                    "antenna": self._antenna,
                    "read_power": self._read_power,
                },
                "last_export_file": self._last_export_file,
                "debug_lines": list(self._debug),
            }
        last = self._storage.last_finished()
        state["last_session"] = (
            {
                "id": last["id"],
                "name": last["name"],
                "started_at": last["started_at"],
                "ended_at": last["ended_at"],
                "attendee_count": last["attendee_count"],
            }
            if last
            else None
        )
        return state

    def _export_target(self, session_id: Optional[str]) -> str:
        if session_id:
            return session_id
        with self._lock:
            if self._session_id:
                return self._session_id
        last = self._storage.last_finished()
        if last:
            return last["id"]
        raise RuntimeError("No session to export yet.")

    def csv_for_session(self, session_id: Optional[str] = None) -> tuple[str, str]:
        """Return (filename, csv_text) for a session (default: current/most recent)."""
        sid = self._export_target(session_id)
        sess = self._storage.session(sid)
        if sess is None:
            raise RuntimeError(f"Unknown session: {sid}")
        rows = self._storage.attendees(sid)

        with_roster = bool(self._roster)
        buf = io.StringIO()
        writer = csv.writer(buf)
        header = ["session_id", "session_name", "epc", "scanned_at"]
        if with_roster:
            header += ["student_number", "name"]
        writer.writerow(header)
        for row in rows:
            out = [sid, sess["name"], row["epc"], row["scanned_at"]]
            if with_roster:
                entry = self._roster.get(row["epc"], {})
                out += [entry.get("student_number", ""), entry.get("name", "")]
            writer.writerow(out)

        base = f"attendance_{safe_filename(sess['name'])}_{sid}" if sess["name"] else f"attendance_{sid}"
        return f"{base}.csv", buf.getvalue()

    def export_csv(self, session_id: Optional[str] = None) -> pathlib.Path:
        filename, text = self.csv_for_session(session_id)
        self._export_dir.mkdir(parents=True, exist_ok=True)
        path = self._export_dir / filename
        path.write_text(text, encoding="utf-8", newline="")
        with self._lock:
            self._last_export_file = filename
        return path

    def sessions(self) -> list[dict[str, Any]]:
        return self._storage.sessions_with_counts()


def create_app(args: argparse.Namespace) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(APP_DIR / "templates"),
        static_folder=str(APP_DIR / "static"),
    )

    data_dir = pathlib.Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = APP_DIR / data_dir
    export_dir = pathlib.Path(args.export_dir)
    if not export_dir.is_absolute():
        export_dir = APP_DIR / export_dir

    storage = Storage(data_dir / "attendance.db")
    recovered = storage.recover_interrupted()
    roster = load_roster(data_dir / "roster.csv")

    readasync_bin = str(pathlib.Path(args.readasync_bin))
    if not pathlib.Path(readasync_bin).is_absolute():
        readasync_bin = str(APP_DIR / readasync_bin)

    if args.mock:
        def reader_factory(**kwargs: Any) -> MockReadProcess:
            return MockReadProcess(
                on_tag=kwargs["on_tag"], on_line=kwargs["on_line"], on_exit=kwargs["on_exit"]
            )
    else:
        def reader_factory(**kwargs: Any) -> ReadAsyncProcess:
            if not pathlib.Path(readasync_bin).exists():
                raise RuntimeError("readasync is not built — run ./install.sh on the Pi first.")
            return ReadAsyncProcess(readasync_bin=readasync_bin, **kwargs)

    controller = AttendanceController(
        storage=storage,
        export_dir=export_dir,
        default_uri=args.uri,
        default_antenna=args.antenna,
        default_read_power=args.read_power,
        reader_factory=reader_factory,
        mock=args.mock,
        roster=roster,
    )
    app.extensions["controller"] = controller
    if recovered:
        print(f"Recovered {recovered} interrupted session(s) from a previous run.", flush=True)
    if roster:
        print(f"Loaded roster with {len(roster)} tag(s).", flush=True)

    @app.get("/")
    def home() -> str:
        return render_template("index.html")

    @app.get("/api/state")
    def api_state() -> Response:
        state = controller.snapshot()
        state["exports_page_url"] = f"{export_base_url(args.port)}/exports"
        return jsonify(state)

    @app.post("/api/start")
    def api_start() -> Response:
        data = request.get_json(silent=True) or {}
        controller.start(
            uri=(data.get("uri") or "").strip() or None,
            antenna=int(data["antenna"]) if data.get("antenna") not in (None, "") else None,
            read_power=int(data["read_power"]) if data.get("read_power") not in (None, "") else None,
            session_name=str(data.get("session_name") or ""),
        )
        return jsonify({"ok": True})

    @app.post("/api/pause")
    def api_pause() -> Response:
        controller.pause()
        return jsonify({"ok": True})

    @app.post("/api/stop")
    def api_stop() -> Response:
        controller.stop()
        return jsonify({"ok": True})

    @app.post("/api/export")
    def api_export() -> Response:
        data = request.get_json(silent=True) or {}
        path = controller.export_csv(session_id=(data.get("session_id") or None))
        return jsonify(
            {
                "ok": True,
                "filename": path.name,
                "download_url": f"/downloads/{path.name}",
                "exports_page_url": f"{export_base_url(args.port)}/exports",
            }
        )

    @app.get("/api/sessions")
    def api_sessions() -> Response:
        return jsonify({"sessions": controller.sessions()})

    @app.get("/exports")
    def exports_page() -> str:
        return render_template("exports.html", sessions=controller.sessions())

    @app.get("/export/<session_id>.csv")
    def export_download(session_id: str) -> Response:
        filename, text = controller.csv_for_session(session_id)
        return Response(
            text,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @app.get("/downloads/<path:filename>")
    def download(filename: str) -> Response:
        return send_from_directory(str(export_dir), filename, as_attachment=True)

    @app.errorhandler(Exception)
    def on_error(exc: Exception):
        if isinstance(exc, HTTPException):
            return exc
        status = 400 if isinstance(exc, (RuntimeError, ValueError)) else 500
        return jsonify({"ok": False, "error": str(exc)}), status

    return app


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Attendance kiosk for the M7E Hecto UHF reader.")
    parser.add_argument("--host", default=os.getenv("ATTENDANCE_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ATTENDANCE_PORT", "8080")))
    parser.add_argument("--uri", default=os.getenv("ATTENDANCE_URI", "tmr:///dev/hecto"))
    parser.add_argument("--antenna", type=int, default=int(os.getenv("ATTENDANCE_ANTENNA", "1")))
    parser.add_argument("--read-power", type=int, default=int(os.getenv("ATTENDANCE_READ_POWER", "1900")))
    parser.add_argument(
        "--readasync-bin",
        default=os.getenv("ATTENDANCE_READASYNC_BIN", "c/src/api/readasync"),
    )
    parser.add_argument("--export-dir", default=os.getenv("ATTENDANCE_EXPORT_DIR", "exports"))
    parser.add_argument("--data-dir", default=os.getenv("ATTENDANCE_DATA_DIR", "data"))
    parser.add_argument(
        "--mock",
        action="store_true",
        default=os.getenv("ATTENDANCE_MOCK", "0") == "1",
        help="Use a fake tag reader (development without hardware).",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    app = create_app(args)
    print(f"Attendance kiosk on http://{local_ip()}:{args.port} (mock={args.mock})", flush=True)
    try:
        app.run(host=args.host, port=args.port, debug=False, threaded=True)
    finally:
        app.extensions["controller"].shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
