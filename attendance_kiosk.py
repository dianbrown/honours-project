#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import pathlib
import queue
import re
import socket
import subprocess
import threading
from dataclasses import dataclass, field
from email.message import EmailMessage
import smtplib
from typing import Any, Optional

from flask import Flask, jsonify, render_template, request, send_from_directory


TAG_LINE_RE = re.compile(
    r"Background read: Tag ID:(?P<epc>[0-9A-F]+)\s+ant:(?P<ant>\d+)\s+count:(?P<count>\d+)\s+time:(?P<reader_time>.+)"
)


def iso_now() -> str:
    return dt.datetime.now().isoformat(timespec="milliseconds")


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


class ReadAsyncProcess:
    def __init__(
        self,
        readasync_bin: str,
        uri: str,
        antenna: int,
        read_power: int,
        on_tag,
        on_line,
        on_exit,
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
        exe_dir = str(exe_path.parent)
        path_parts = [exe_dir]

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
        for raw in self._process.stdout:
            line = raw.strip()
            if not line:
                continue
            self._on_line(line)
            m = TAG_LINE_RE.search(line)
            if m:
                self._on_tag(
                    {
                        "host_timestamp": iso_now(),
                        "epc": m.group("epc"),
                        "antenna": int(m.group("ant")),
                        "read_count": int(m.group("count")),
                        "reader_timestamp": m.group("reader_time").strip(),
                    }
                )
            if self._stop.is_set():
                break
        rc = self._process.poll()
        if rc is None:
            return
        self._on_line(f"Reader process exited with code {rc}")
        self._on_exit(rc)

    def stop(self) -> None:
        self._stop.set()
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
        self._thread = None


@dataclass
class SessionState:
    status: str = "idle"  # idle | running | paused | stopped
    session_id: str = ""
    started_at: str = ""
    ended_at: str = ""
    uri: str = ""
    antenna: int = 1
    read_power: int = 1900
    tags: list[dict[str, Any]] = field(default_factory=list)
    logs: queue.Queue[str] = field(default_factory=queue.Queue)
    last_export_file: str = ""


class AttendanceController:
    def __init__(
        self,
        readasync_bin: str,
        export_dir: pathlib.Path,
        default_uri: str,
        default_antenna: int,
        default_read_power: int,
    ) -> None:
        self._lock = threading.Lock()
        self._readasync_bin = readasync_bin
        self._export_dir = export_dir
        self._default_uri = default_uri
        self._state = SessionState(uri=default_uri, antenna=default_antenna, read_power=default_read_power)
        self._reader: Optional[ReadAsyncProcess] = None

    def _on_tag(self, tag: dict[str, Any]) -> None:
        with self._lock:
            if self._state.status == "running":
                self._state.tags.append(tag)

    def _on_line(self, line: str) -> None:
        with self._lock:
            try:
                self._state.logs.put_nowait(f"[{iso_now()}] {line}")
            except Exception:
                pass

    def _on_reader_exit(self, rc: int) -> None:
        with self._lock:
            if self._state.status == "running":
                self._state.status = "stopped"
                self._state.ended_at = iso_now()
            try:
                self._state.logs.put_nowait(f"[{iso_now()}] Reader exited ({rc}).")
            except Exception:
                pass
            self._reader = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            unique = len({row["epc"] for row in self._state.tags})
            logs: list[str] = []
            while True:
                try:
                    logs.append(self._state.logs.get_nowait())
                except queue.Empty:
                    break
            return {
                "status": self._state.status,
                "session_id": self._state.session_id,
                "started_at": self._state.started_at,
                "ended_at": self._state.ended_at,
                "uri": self._state.uri,
                "antenna": self._state.antenna,
                "read_power": self._state.read_power,
                "tag_count": len(self._state.tags),
                "unique_tag_count": unique,
                "tags": self._state.tags[-200:],
                "new_logs": logs,
                "last_export_file": self._state.last_export_file,
            }

    def start(self, uri: Optional[str], antenna: int, read_power: int) -> None:
        with self._lock:
            if self._state.status == "running":
                raise RuntimeError("Session already running.")
            if self._state.status in ("idle", "stopped"):
                sid = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                self._state = SessionState(
                    status="running",
                    session_id=sid,
                    started_at=iso_now(),
                    uri=uri or self._default_uri,
                    antenna=antenna,
                    read_power=read_power,
                )
            elif self._state.status == "paused":
                self._state.status = "running"
                self._state.uri = uri or self._state.uri
                self._state.antenna = antenna
                self._state.read_power = read_power
            else:
                raise RuntimeError(f"Cannot start session in state: {self._state.status}")

            self._reader = ReadAsyncProcess(
                readasync_bin=self._readasync_bin,
                uri=self._state.uri,
                antenna=self._state.antenna,
                read_power=self._state.read_power,
                on_tag=self._on_tag,
                on_line=self._on_line,
                on_exit=self._on_reader_exit,
            )
            reader = self._reader
        try:
            assert reader is not None
            reader.start()
        except Exception:
            with self._lock:
                self._reader = None
                self._state.status = "idle" if not self._state.tags else "stopped"
            raise

    def pause(self) -> None:
        with self._lock:
            if self._state.status != "running":
                raise RuntimeError("Session is not running.")
            self._state.status = "paused"
            reader = self._reader
            self._reader = None
        if reader is not None:
            reader.stop()

    def stop(self) -> None:
        with self._lock:
            if self._state.status not in ("running", "paused"):
                raise RuntimeError("Session is not running/paused.")
            self._state.status = "stopped"
            self._state.ended_at = iso_now()
            reader = self._reader
            self._reader = None
        if reader is not None:
            reader.stop()

    def export_csv(self) -> pathlib.Path:
        with self._lock:
            if not self._state.session_id:
                raise RuntimeError("No session exists yet.")
            self._export_dir.mkdir(parents=True, exist_ok=True)
            filename = f"attendance_{safe_filename(self._state.session_id)}.csv"
            path = self._export_dir / filename
            rows = list(self._state.tags)

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["session_id", "host_timestamp", "epc", "antenna", "read_count", "reader_timestamp"])
            for row in rows:
                writer.writerow(
                    [
                        self._state.session_id,
                        row.get("host_timestamp", ""),
                        row.get("epc", ""),
                        row.get("antenna", ""),
                        row.get("read_count", ""),
                        row.get("reader_timestamp", ""),
                    ]
                )

        with self._lock:
            self._state.last_export_file = path.name
        return path

    def send_email(self, to_email: str, csv_path: pathlib.Path) -> None:
        host = os.getenv("ATTENDANCE_SMTP_HOST", "")
        port = int(os.getenv("ATTENDANCE_SMTP_PORT", "587"))
        username = os.getenv("ATTENDANCE_SMTP_USER", "")
        password = os.getenv("ATTENDANCE_SMTP_PASS", "")
        from_email = os.getenv("ATTENDANCE_SMTP_FROM", username)
        use_tls = os.getenv("ATTENDANCE_SMTP_TLS", "1") != "0"

        if not host or not username or not password or not from_email:
            raise RuntimeError("SMTP env vars missing. Set ATTENDANCE_SMTP_HOST/USER/PASS/FROM.")

        msg = EmailMessage()
        msg["Subject"] = "Attendance CSV Export"
        msg["From"] = from_email
        msg["To"] = to_email
        msg.set_content("Attached is your attendance export CSV.")
        data = csv_path.read_bytes()
        msg.add_attachment(data, maintype="text", subtype="csv", filename=csv_path.name)

        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if use_tls:
                smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(msg)


def create_app(args: argparse.Namespace) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(pathlib.Path(__file__).parent / "templates"),
        static_folder=str(pathlib.Path(__file__).parent / "static"),
    )

    controller = AttendanceController(
        readasync_bin=args.readasync_bin,
        export_dir=pathlib.Path(args.export_dir),
        default_uri=args.uri,
        default_antenna=args.antenna,
        default_read_power=args.read_power,
    )

    @app.get("/")
    def home():
        return render_template(
            "index.html",
            default_uri=args.uri,
            default_antenna=args.antenna,
            default_power=args.read_power,
            local_ip=local_ip(),
            port=args.port,
        )

    @app.get("/api/state")
    def api_state():
        return jsonify(controller.snapshot())

    @app.post("/api/start")
    def api_start():
        data = request.get_json(silent=True) or {}
        uri = (data.get("uri") or "").strip() or None
        antenna = int(data.get("antenna", args.antenna))
        read_power = int(data.get("read_power", args.read_power))
        controller.start(uri=uri, antenna=antenna, read_power=read_power)
        return jsonify({"ok": True})

    @app.post("/api/pause")
    def api_pause():
        controller.pause()
        return jsonify({"ok": True})

    @app.post("/api/stop")
    def api_stop():
        controller.stop()
        return jsonify({"ok": True})

    @app.post("/api/export")
    def api_export():
        path = controller.export_csv()
        return jsonify({"ok": True, "filename": path.name, "download_url": f"/downloads/{path.name}"})

    @app.post("/api/email")
    def api_email():
        data = request.get_json(silent=True) or {}
        to_email = (data.get("to_email") or "").strip()
        if not to_email:
            return jsonify({"ok": False, "error": "to_email is required"}), 400

        filename = (data.get("filename") or "").strip()
        export_dir = pathlib.Path(args.export_dir)
        if filename:
            csv_path = export_dir / filename
            if not csv_path.exists():
                return jsonify({"ok": False, "error": "CSV file not found"}), 404
        else:
            csv_path = controller.export_csv()

        controller.send_email(to_email=to_email, csv_path=csv_path)
        return jsonify({"ok": True})

    @app.get("/downloads/<path:filename>")
    def download(filename: str):
        return send_from_directory(args.export_dir, filename, as_attachment=True)

    @app.errorhandler(Exception)
    def on_error(exc: Exception):
        return jsonify({"ok": False, "error": str(exc)}), 500

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Attendance kiosk web app for M7E Hecto.")
    parser.add_argument("--host", default=os.getenv("ATTENDANCE_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ATTENDANCE_PORT", "8080")))
    parser.add_argument("--uri", default=os.getenv("ATTENDANCE_URI", "tmr:///dev/ttyUSB0"))
    parser.add_argument("--antenna", type=int, default=int(os.getenv("ATTENDANCE_ANTENNA", "1")))
    parser.add_argument("--read-power", type=int, default=int(os.getenv("ATTENDANCE_READ_POWER", "1900")))
    parser.add_argument(
        "--readasync-bin",
        default=os.getenv("ATTENDANCE_READASYNC_BIN", "c/src/api/readasync"),
    )
    parser.add_argument("--export-dir", default=os.getenv("ATTENDANCE_EXPORT_DIR", "exports"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = create_app(args)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
