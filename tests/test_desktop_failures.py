import copy
import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import desktop_backend
import courses
from tests.test_desktop import dashboard, add
from tests.test_monitor import record
from monitor import determine_status
import monitor
from desktop_worker import WorkerControl
from dataclasses import asdict


def test_failed_start_save_cleans_up_child(dashboard):
    key = add(dashboard)
    before = dashboard.snapshot()
    process = MagicMock()
    dashboard._spawn = MagicMock(return_value=process)
    with patch("desktop_backend.threading.Thread"), patch.object(dashboard, "_save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            dashboard.start(key)
    process.stdin.write.assert_called_with("stop\n")
    process.wait.assert_called_with(timeout=8)
    assert not dashboard._processes
    assert dashboard.snapshot() == before


def test_malformed_and_unknown_events_do_not_end_reader(dashboard):
    key = add(dashboard)
    process = MagicMock()
    status = asdict(determine_status(record()))
    process.stdout = iter(json.dumps(event) for event in [
        [], {"kind": "future"}, {"kind": "status", "status": {}},
        {"kind": "waiting", "next_check_at": float("nan")},
        {"kind": "status", "status": status, "checked_at": "2026-09-12T00:00:00Z"},
    ])
    process.wait.return_value = 1
    dashboard._processes[key] = process
    dashboard._read_events(key, process)
    assert dashboard._find(key)["status"] == status
    assert sum(e["kind"] == "error" for e in dashboard.data["activity"]) == 3


def test_reader_ignores_replaced_process(dashboard):
    key = add(dashboard)
    previous, current = MagicMock(), MagicMock()
    previous.stdout = iter([json.dumps({"kind": "signin"})])
    dashboard._processes[key] = current
    before = copy.deepcopy(dashboard.data)
    dashboard._read_events(key, previous)
    assert dashboard.data == before
    previous.wait.assert_not_called()
    assert dashboard._processes[key] is current


def test_shutdown_attempts_all_workers_and_keeps_failed_worker(dashboard):
    first, second = add(dashboard, 1), add(dashboard, 2)
    processes = [MagicMock(), MagicMock()]
    dashboard._processes.update(zip([first, second], processes))
    with patch("desktop_backend.stop_process", side_effect=[subprocess.TimeoutExpired("worker", 10), None]) as stop:
        with pytest.raises(OSError):
            dashboard.shutdown()
    assert stop.call_count == 2
    assert dashboard._processes == {first: processes[0]}
    assert dashboard._find(first)["state"] == "error"
    with patch("desktop_backend.stop_process"):
        dashboard.shutdown()
    assert not dashboard._processes


def test_broken_stop_pipe_still_forces_termination():
    process = MagicMock()
    process.stdin.write.side_effect = BrokenPipeError
    process.wait.side_effect = [subprocess.TimeoutExpired("worker", 8), 0]
    with patch("desktop_backend.subprocess.run") as kill:
        desktop_backend.stop_process(process)
    if desktop_backend.os.name == "nt":
        assert kill.call_args.args[0] == ["taskkill", "/PID", str(process.pid), "/T", "/F"]
    else:
        process.kill.assert_called_once()
    process.stdin.close.assert_called_once()


def test_broken_check_pipe_does_not_change_schedule(dashboard):
    key = add(dashboard)
    item = dashboard._find(key)
    item.update(state="running", check_phase="waiting", next_check_at=123)
    before = copy.deepcopy(item)
    process = MagicMock()
    process.stdin.write.side_effect = BrokenPipeError
    dashboard._processes[key] = process
    with pytest.raises(BrokenPipeError):
        dashboard.check_now(key)
    assert item == before


@pytest.mark.parametrize("field,value", [
    ("interval", "60"), ("section_id", None), ("status", {"enrolled": 1}),
    ("lecture", []), ("checked_at", "yesterday"), ("notification_delivery", {"state": "delivered"}),
])
def test_invalid_saved_class_is_not_rewritten(dashboard, field, value):
    add(dashboard)
    data = copy.deepcopy(dashboard.data)
    data["classes"][0][field] = value
    original = json.dumps(data)
    dashboard.path.write_text(original)
    with pytest.raises(ValueError):
        desktop_backend.Dashboard(dashboard.path)
    assert dashboard.path.read_text() == original


def test_invalid_activity_is_not_rewritten(dashboard):
    data = {**dashboard.data, "activity": [{"at": 5}]}
    original = json.dumps(data)
    dashboard.path.write_text(original)
    with pytest.raises(ValueError):
        desktop_backend.Dashboard(dashboard.path)
    assert dashboard.path.read_text() == original


@pytest.mark.parametrize("operation", ["theme", "default", "tray", "edit", "add", "move", "remove"])
def test_failed_settings_replace_keeps_memory_and_file(dashboard, operation):
    first, second = add(dashboard, 1), add(dashboard, 2)
    before = copy.deepcopy(dashboard.data)
    original = dashboard.path.read_bytes()
    actions = {
        "theme": lambda: dashboard.set_theme("dark"),
        "default": lambda: dashboard.set_default("muted"),
        "tray": dashboard.mark_tray_notice,
        "edit": lambda: dashboard.save_class({**dashboard._find(first), "interval": 120}),
        "add": lambda: add(dashboard, 3),
        "move": lambda: dashboard.move_class(second, "earlier"),
        "remove": lambda: dashboard.remove(first),
    }
    with patch("pathlib.Path.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            actions[operation]()
    assert dashboard.data == before
    assert dashboard.path.read_bytes() == original


def test_reload_preserves_unknown_fields_and_optional_defaults(dashboard):
    add(dashboard)
    data = copy.deepcopy(dashboard.data)
    data["future"] = {"keep": True}
    item = data["classes"][0]
    item["future"] = [1, 2]
    item["status"] = {**asdict(determine_status(record())), "future": 1}
    for name in ("mode", "interval", "parent", "notification", "continuous"):
        item.pop(name)
    dashboard.path.write_text(json.dumps(data))
    loaded = desktop_backend.Dashboard(dashboard.path).data
    assert loaded["future"] == data["future"]
    assert loaded["classes"][0]["future"] == [1, 2]
    assert loaded["classes"][0]["status"]["future"] == 1
    assert loaded["classes"][0]["interval"] == 60
    assert loaded["classes"][0]["state"] == "paused"


def test_invalid_legacy_profile_is_preserved(tmp_path):
    path = tmp_path / "profiles.json"
    original = '[{"url":"https://classes.berkeley.edu/content/a", "label":"Missing identity"}]'
    path.write_text(original)
    with pytest.raises(ValueError):
        courses.read_profiles(path)
    assert path.read_text() == original


@pytest.mark.parametrize("error,delivery", [
    (monitor.MonitorError("smtp webhook email in a page error"), False),
    (monitor.NotificationError("arbitrary wording with a SECRET"), True),
])
def test_error_classification_does_not_depend_on_log_words(error, delivery):
    with patch("monitor.report_event") as event:
        monitor.report_failure(error)
    assert event.call_args.kwargs["notification_error"] is delivery
    assert "SECRET" not in str(event.call_args)


def test_cancellation_checkpoint_interrupts_browser_and_waiting():
    control = WorkerControl()
    control.stop()
    with pytest.raises(KeyboardInterrupt):
        control.wait(30, MagicMock())
    with patch("monitor.cancellation_checkpoint", control.checkpoint):
        page = MagicMock()
        with pytest.raises(KeyboardInterrupt):
            monitor.wait_for_enrollment_entry(page)
        with pytest.raises(KeyboardInterrupt):
            monitor.complete_calcentral_login(page)
        page.get_by_role.assert_not_called()


def test_notification_failure_publishes_one_structured_error(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTIFICATION_MODE", "seats")
    path = tmp_path / "state.json"
    monitor.save_previous_status(path, determine_status(record()))
    from tests.test_monitor import page
    with patch("monitor.fetch_page", return_value=page(record("O", "Open", 39))), \
         patch("monitor.send_notification", side_effect=monitor.MonitorError("arbitrary failure")), \
         patch("monitor.report_event") as event, patch("monitor.wait_for_next_check", side_effect=KeyboardInterrupt):
        monitor.run_continuously("https://example.test", "27743", path, 30)
    failures = [call for call in event.call_args_list if call.args == ("error",)]
    assert len(failures) == 1
    assert failures[0].kwargs["notification_error"] is True


def test_notification_configuration_uses_one_result(dashboard):
    with patch.dict(desktop_backend.os.environ, {"NOTIFICATION_WEBHOOK_URL": "   "}, clear=True):
        snapshot = dashboard.snapshot()
    assert snapshot["notification_configured"] is snapshot["notifications"]["configured"] is False


def test_reader_recovers_after_dashboard_write_failure(dashboard):
    key = add(dashboard)
    process = MagicMock()
    process.stdout = iter(json.dumps(event) for event in [
        {"kind": "status", "status": asdict(determine_status(record())), "checked_at": "2026-09-12T00:00:00Z"},
        {"kind": "status", "status": asdict(determine_status(record("O", "Open", 39))), "checked_at": "2026-09-12T00:01:00Z"},
    ])
    process.wait.return_value = 0
    dashboard._processes[key] = process
    save = dashboard._save
    attempts = 0

    def fail_first_save():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("disk full")
        save()

    with patch.object(dashboard, "_save", side_effect=fail_first_save):
        dashboard._read_events(key, process)
    assert dashboard._find(key)["status"]["enrolled"] == 39
    assert attempts == 3
    assert "disk" not in dashboard._find(key)["error"]


def test_failed_reader_start_cleans_up_child(dashboard):
    key = add(dashboard)
    process = MagicMock()
    dashboard._spawn = MagicMock(return_value=process)
    with patch("desktop_backend.threading.Thread.start", side_effect=RuntimeError("no thread")):
        with pytest.raises(RuntimeError):
            dashboard.start(key)
    process.wait.assert_called_once_with(timeout=8)
    assert not dashboard._processes


def test_eof_with_living_child_has_bounded_cleanup(dashboard):
    key = add(dashboard)
    process = MagicMock()
    process.stdout = iter([])
    process.wait.side_effect = subprocess.TimeoutExpired("worker", 8)
    dashboard._processes[key] = process
    with patch("desktop_backend.stop_process", side_effect=subprocess.TimeoutExpired("worker", 10)) as stop:
        dashboard._read_events(key, process)
    stop.assert_called_once_with(process)
    assert dashboard._processes[key] is process
    assert dashboard._find(key)["state"] == "error"
