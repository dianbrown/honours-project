"""Unit tests for the attendance kiosk backend. Run with: pytest"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import attendance_kiosk as ak


# ---------- helpers ----------

class DummyReader:
    """Reader stand-in that never emits on its own; tests feed tags directly."""

    instances: list["DummyReader"] = []

    def __init__(self, fail_start: bool = False, **kwargs):
        self.fail_start = fail_start
        self.started = False
        self.stopped = False
        DummyReader.instances.append(self)

    def start(self):
        if self.fail_start:
            raise RuntimeError("boom")
        self.started = True

    def stop(self):
        self.stopped = True


@pytest.fixture
def storage(tmp_path):
    return ak.Storage(tmp_path / "test.db")


@pytest.fixture
def controller(storage, tmp_path):
    DummyReader.instances = []

    def factory(**kwargs):
        return DummyReader()

    return ak.AttendanceController(
        storage=storage,
        export_dir=tmp_path / "exports",
        default_uri="mock://",
        default_antenna=1,
        default_read_power=1900,
        reader_factory=factory,
        mock=True,
    )


def tag(epc: str) -> dict:
    return {"epc": epc, "antenna": 1, "read_count": 1, "reader_timestamp": "t"}


# ---------- stdout parsing ----------

def test_tag_line_regex_parses_readasync_output():
    line = "Background read: Tag ID:E28068940000501234567890 ant:1 count:3 time:2026-07-10T09:00:01.123"
    m = ak.TAG_LINE_RE.search(line)
    assert m is not None
    assert m.group("epc") == "E28068940000501234567890"
    assert m.group("ant") == "1"
    assert m.group("count") == "3"
    assert m.group("reader_time") == "2026-07-10T09:00:01.123"


def test_tag_line_regex_ignores_other_lines():
    assert ak.TAG_LINE_RE.search("Reader stats: temp 32C") is None


# ---------- scan-once ----------

def test_scan_once_same_epc_recorded_only_once(controller):
    controller.start(session_name="test")
    for _ in range(5):
        controller._on_tag(tag("AAAA000000000000000000BB"))
    controller._on_tag(tag("CCCC000000000000000000DD"))
    snap = controller.snapshot()
    assert snap["scanned_count"] == 2
    epcs = [t["epc"] for t in snap["tags"]]
    assert epcs == ["AAAA000000000000000000BB", "CCCC000000000000000000DD"]


def test_tags_ignored_while_paused(controller):
    controller.start()
    controller._on_tag(tag("AAAA000000000000000000BB"))
    controller.pause()
    controller._on_tag(tag("CCCC000000000000000000DD"))
    assert controller.snapshot()["scanned_count"] == 1
    controller.start()  # resume
    controller._on_tag(tag("CCCC000000000000000000DD"))
    assert controller.snapshot()["scanned_count"] == 2


# ---------- state machine ----------

def test_full_lifecycle(controller):
    assert controller.snapshot()["status"] == "idle"
    controller.start(session_name="lecture")
    assert controller.snapshot()["status"] == "running"
    sid = controller.snapshot()["session_id"]
    controller.pause()
    assert controller.snapshot()["status"] == "paused"
    controller.start()  # resume keeps the same session
    assert controller.snapshot()["session_id"] == sid
    controller.stop()
    snap = controller.snapshot()
    assert snap["status"] == "stopped"
    assert snap["ended_at"]


def test_illegal_transitions_raise(controller):
    with pytest.raises(RuntimeError):
        controller.pause()  # not running
    with pytest.raises(RuntimeError):
        controller.stop()  # nothing to stop
    controller.start()
    with pytest.raises(RuntimeError):
        controller.start()  # already running


def test_failed_reader_start_rolls_back(storage, tmp_path):
    def factory(**kwargs):
        return DummyReader(fail_start=True)

    c = ak.AttendanceController(
        storage=storage,
        export_dir=tmp_path / "exports",
        default_uri="mock://",
        default_antenna=1,
        default_read_power=1900,
        reader_factory=factory,
        mock=True,
    )
    with pytest.raises(RuntimeError):
        c.start()
    assert c.snapshot()["status"] == "idle"
    assert storage.sessions_with_counts() == []  # empty session row cleaned up


def test_reader_stopped_on_pause_and_stop(controller):
    controller.start()
    reader = DummyReader.instances[-1]
    controller.pause()
    assert reader.stopped
    controller.start()
    reader2 = DummyReader.instances[-1]
    controller.stop()
    assert reader2.stopped


# ---------- persistence & crash recovery ----------

def test_attendees_persist_and_crash_recovery(tmp_path):
    db = tmp_path / "att.db"
    storage = ak.Storage(db)
    storage.create_session("S1", "Lecture", "2026-07-10T09:00:00", "mock://", 1, 1900)
    storage.add_attendee("S1", "AAAA", "2026-07-10T09:01:00")
    storage.add_attendee("S1", "AAAA", "2026-07-10T09:02:00")  # duplicate → ignored
    storage.add_attendee("S1", "BBBB", "2026-07-10T09:03:00")
    storage.close()

    # simulate app restart after a power cut (session left 'running')
    storage2 = ak.Storage(db)
    assert storage2.recover_interrupted() == 1
    last = storage2.last_finished()
    assert last["id"] == "S1"
    assert last["status"] == "stopped"
    assert last["ended_at"]
    assert last["attendee_count"] == 2
    rows = storage2.attendees("S1")
    assert [r["epc"] for r in rows] == ["AAAA", "BBBB"]
    assert rows[0]["scanned_at"] == "2026-07-10T09:01:00"  # first scan wins
    storage2.close()


# ---------- CSV export ----------

def test_csv_export_content(controller, tmp_path):
    controller.start(session_name="CSC311 Tue")
    controller._on_tag(tag("AAAA000000000000000000BB"))
    controller._on_tag(tag("CCCC000000000000000000DD"))
    controller.stop()

    path = controller.export_csv()
    assert path.exists()
    lines = path.read_text().strip().splitlines()
    assert lines[0] == "session_id,session_name,epc,scanned_at"
    assert len(lines) == 3
    assert "CSC311 Tue" in lines[1]
    assert "AAAA000000000000000000BB" in lines[1]
    assert "CCCC000000000000000000DD" in lines[2]
    assert "CSC311_Tue" in path.name


def test_csv_export_with_roster(storage, tmp_path):
    def factory(**kwargs):
        return DummyReader()

    roster = {"AAAA000000000000000000BB": {"student_number": "u21000001", "name": "Alice"}}
    c = ak.AttendanceController(
        storage=storage,
        export_dir=tmp_path / "exports",
        default_uri="mock://",
        default_antenna=1,
        default_read_power=1900,
        reader_factory=factory,
        mock=True,
        roster=roster,
    )
    c.start()
    c._on_tag(tag("AAAA000000000000000000BB"))
    c._on_tag(tag("EEEE000000000000000000FF"))
    c.stop()
    _, text = c.csv_for_session()
    lines = text.strip().splitlines()
    assert lines[0].endswith("student_number,name")
    assert "u21000001,Alice" in lines[1]
    assert lines[2].endswith(",,")  # unknown tag → empty roster columns


def test_export_without_sessions_raises(controller):
    with pytest.raises(RuntimeError):
        controller.export_csv()


# ---------- misc ----------

def test_safe_filename():
    assert ak.safe_filename("CSC311 Tue 09:00") == "CSC311_Tue_09_00"
    assert ak.safe_filename("///") == "session"


def test_uri_device_roundtrip():
    assert ak.uri_to_device("tmr:///dev/ttyUSB0") == "/dev/ttyUSB0"
    assert ak.device_to_uri("/dev/ttyUSB0") == "tmr:///dev/ttyUSB0"
    assert ak.device_to_uri("tmr:///dev/hecto") == "tmr:///dev/hecto"
