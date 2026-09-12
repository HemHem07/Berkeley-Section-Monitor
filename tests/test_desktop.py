import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

import desktop_backend
import monitor
from desktop import Api, dashboard_html
from tests.test_multiple import course
from desktop_worker import WorkerControl


@pytest.fixture
def dashboard(tmp_path):
    with patch("desktop_backend.read_profiles", return_value=[]):
        return desktop_backend.Dashboard(tmp_path / "dashboard.json")


def add(dashboard, number=1, **settings):
    profile = course(number)
    with patch("desktop_backend.discover_course", return_value=profile):
        return dashboard.save_class(dict(url=profile["url"], mode="public", interval=30,
                                         notification="default", **settings))


@pytest.mark.parametrize("policy,expected", [("seats",True),("waitlist",False),("changes",True),("muted",False)])
def test_notification_policies_for_seat_opening(monkeypatch, policy, expected):
    monkeypatch.setenv("NOTIFICATION_MODE", policy)
    closed = monitor.SectionStatus("1", "C", "Closed", 40, 40, 0, 6, 0, False)
    opened = replace(closed, status_code="O", status_description="Open", enrolled=39, is_open=True)
    assert monitor.should_notify(closed, opened) is expected
    assert monitor.should_notify(None, opened) is False
    assert monitor.should_notify(opened, opened) is False


def test_waitlist_notifications_require_a_transition(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_MODE", "waitlist")
    full = monitor.SectionStatus("1", "W", "Waitlist", 40, 40, 6, 6, 0, False)
    available = replace(full, waitlisted=5)
    assert monitor.should_notify(full, available)
    assert not monitor.should_notify(available, replace(available, waitlisted=4))


def test_desktop_suppresses_startup_webhooks(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_MODE", "seats")
    with patch("monitor.requests.post") as send:
        assert monitor.send_startup_notification("Public", 30, True)
    send.assert_not_called()


def test_next_check_deadline_is_published_before_waiting():
    with patch("monitor.report_event") as report, patch("monitor.time.sleep") as sleep:
        before = time.time()
        monitor.wait_for_next_check(60)
        after = time.time()
    assert report.call_args.args == ("waiting",)
    assert before + 60 - .001 <= report.call_args.kwargs["next_check_at"] <= after + 60 + .001
    sleep.assert_called_once_with(60)


def test_signin_observer_remains_enabled_when_muted(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_MODE", "muted")
    monkeypatch.delenv("NOTIFICATION_WEBHOOK_URL", raising=False)
    with patch("monitor.report_event") as report:
        monitor.send_signin_notification()
    report.assert_called_once_with("signin")


def test_failed_signin_alert_does_not_block_desktop_login(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTIFICATION_MODE", "muted")
    browser = MagicMock()
    browser.chromium.launch_persistent_context.return_value.pages = [MagicMock()]
    with patch("playwright.sync_api.sync_playwright") as runtime, \
         patch("monitor.fetch_calcentral_status", side_effect=monitor.SignInRequired()), \
         patch("monitor.send_signin_notification", side_effect=monitor.MonitorError("delivery failed")), \
         patch("monitor.complete_calcentral_login", side_effect=KeyboardInterrupt) as login:
        runtime.return_value.__enter__.return_value = browser
        with pytest.raises(KeyboardInterrupt):
            monitor.run_calcentral("27743", "104", tmp_path / "state.json", 30, True)
    login.assert_called_once()


def test_profiles_persist_and_restart_paused(dashboard):
    key = add(dashboard)
    dashboard.set_default("changes")
    reloaded = desktop_backend.Dashboard(dashboard.path)
    assert reloaded.snapshot()["default_notification"] == "changes"
    assert reloaded.snapshot()["classes"][0]["id"] == key
    assert reloaded.snapshot()["classes"][0]["state"] == "paused"
    reloaded.remove(key)
    assert desktop_backend.Dashboard(dashboard.path).snapshot()["classes"] == []


def test_settings_edit_does_not_require_network_and_duplicate_rejected(dashboard):
    key = add(dashboard)
    with pytest.raises(ValueError, match="already"):
        add(dashboard)
    item = dashboard.snapshot()["classes"][0]
    with patch("desktop_backend.discover_course", side_effect=AssertionError("must not fetch")):
        dashboard.save_class({**item, "interval":120, "notification":"muted"})
    assert dashboard.snapshot()["classes"][0]["interval"] == 120


@pytest.mark.parametrize("values", [{"interval":1},{"mode":"other"},{"notification":"unknown"}])
def test_invalid_settings_do_not_modify_watchlist(dashboard, values):
    with pytest.raises(ValueError):
        dashboard.save_class(values)
    assert dashboard.snapshot()["classes"] == []


def test_invalid_file_is_not_overwritten(tmp_path):
    path = tmp_path / "dashboard.json"
    path.write_text("not JSON")
    with pytest.raises(ValueError):
        desktop_backend.Dashboard(path)
    assert path.read_text() == "not JSON"


def test_check_now_wakes_wait_without_queueing_checks():
    control = WorkerControl()
    waiting = threading.Event()
    finished = threading.Event()
    control.check_now()  # A request during a fetch must not queue an extra check.
    def wait():
        control.wait(30, lambda *args, **kwargs: waiting.set())
        finished.set()
    thread = threading.Thread(target=wait)
    thread.start()
    try:
        assert waiting.wait(2)
        assert not finished.is_set()
        control.check_now()
        assert finished.wait(2)
        assert not control.waiting
    finally:
        control.stop()
        thread.join(2)


def test_activity_is_bounded_and_persists(dashboard):
    key = add(dashboard)
    item = dashboard._find(key)
    with dashboard._lock:
        for index in range(205):
            dashboard._activity(item, "checked", f"Check {index}")
        dashboard._save()
    saved = desktop_backend.Dashboard(dashboard.path).snapshot()["activity"]
    assert len(saved) == 200
    assert saved[0]["message"] == "Check 204"
    assert saved[-1]["message"] == "Check 5"


def test_activity_records_changes_and_notification_results(dashboard):
    from dataclasses import asdict
    key = add(dashboard)
    first = monitor.SectionStatus("1", "C", "Closed", 40, 40, 0, 6, 0, False)
    second = replace(first, status_code="O", status_description="Open", enrolled=39, is_open=True)
    process = MagicMock()
    process.stdout = iter(json.dumps(event) for event in [
        {"kind":"status", "status":asdict(first), "checked_at":"2026-09-11T12:00:00Z"},
        {"kind":"status", "status":asdict(second), "checked_at":"2026-09-11T12:01:00Z"},
        {"kind":"notified"},
        {"kind":"error", "error":"Notification delivery failed."},
    ])
    process.wait.return_value = 1
    dashboard._processes[key] = process
    dashboard._read_events(key, process)
    events = dashboard.snapshot()["activity"]
    assert any(event["kind"] == "changed" and "Enrolled: 40 → 39" in event["message"] for event in events)
    assert any(event["kind"] == "notified" for event in events)
    assert any(event["kind"] == "error" for event in events)


def test_api_does_not_return_secrets_in_errors(dashboard):
    with patch.object(dashboard, "start", side_effect=OSError("https://secret-webhook")):
        result = Api(dashboard).action("start", {"id":"1"})
    assert not result["ok"]
    assert "secret" not in result["error"]


def test_closing_hides_only_with_a_working_tray(dashboard):
    api = Api(dashboard)
    api._window = MagicMock()
    api._tray = MagicMock()
    api._tray_ready.set()
    assert api._closing() is False
    api._window.hide.assert_called_once()
    assert dashboard.snapshot()["tray_notice_seen"]
    api._window.destroy.assert_not_called()


def test_status_remains_visible_when_notification_fails(tmp_path, monkeypatch):
    from tests.test_monitor import page, record
    monkeypatch.setenv("NOTIFICATION_MODE", "changes")
    path = tmp_path / "state.json"
    previous = monitor.determine_status(record())
    monitor.save_previous_status(path, previous)
    with patch("monitor.fetch_page", return_value=page(record("O", "Open", 39, 40))), patch("monitor.send_notification", side_effect=monitor.MonitorError("offline")), patch("monitor.report_event") as report:
        with pytest.raises(monitor.MonitorError):
            monitor.run_check("https://example.invalid", "27743", path)
    assert report.call_args.kwargs["status"]["enrolled"] == 39
    assert monitor.load_previous_status(path) == previous  # alert must retry


def test_real_worker_lifecycle_and_class_isolation(dashboard, tmp_path):
    harness = tmp_path / "worker_fixture.py"
    harness.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(desktop_backend.ROOT)!r})\n"
        "import desktop_worker, monitor\n"
        "from tests.test_monitor import page, record\n"
        "monitor.fetch_page = lambda *a, **kw: page(record())\n"
        "sys.exit(desktop_worker.main())\n"
    )
    captured = []
    def spawn(command, **kwargs):
        captured.append(copy.deepcopy(kwargs["env"]))
        kwargs["env"]["STATE_FILE"] = str(tmp_path / Path(kwargs["env"]["STATE_FILE"]).name)
        # No real network or notifications in this subprocess integration test.
        kwargs["env"]["NOTIFICATION_MODE"] = "muted"
        return subprocess.Popen([sys.executable, str(harness)], **kwargs)
    dashboard._spawn = spawn
    keys = []
    for number in (1,2):
        profile = course(number)
        profile["section_id"] = "27743"
        with patch("desktop_backend.discover_course", return_value=profile):
            keys.append(dashboard.save_class(dict(url=profile["url"],interval=30,mode="public")))
    try:
        dashboard.start_all()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if all(p["state"] == "running" and p.get("check_phase") == "waiting" for p in dashboard.snapshot()["classes"]):
                break
            time.sleep(.05)
        assert all(p["state"] == "running" for p in dashboard.snapshot()["classes"])
        assert all(time.time() < p["next_check_at"] <= time.time() + 30 for p in dashboard.snapshot()["classes"])
        assert captured[0]["COURSE_URL"] != captured[1]["COURSE_URL"]
        original_times = {p["id"]:p["checked_at"] for p in dashboard.snapshot()["classes"]}
        dashboard.check_now(keys[0])
        dashboard.check_now(keys[0])  # Coalesced while the first request is in flight.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            items = {p["id"]:p for p in dashboard.snapshot()["classes"]}
            if items[keys[0]]["checked_at"] != original_times[keys[0]] and items[keys[0]]["check_phase"] == "waiting":
                break
            time.sleep(.02)
        assert items[keys[0]]["checked_at"] != original_times[keys[0]]
        assert items[keys[1]]["checked_at"] == original_times[keys[1]]
        dashboard.pause(keys[0])
        items = {p["id"]:p for p in dashboard.snapshot()["classes"]}
        assert items[keys[0]]["state"] == "paused"
        assert items[keys[0]]["next_check_at"] is None
        assert items[keys[1]]["state"] == "running"
        assert items[keys[0]]["status"]["enrolled"] == 40
        paused_time = items[keys[0]]["checked_at"]
        dashboard.check_now(keys[0])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            item = next(p for p in dashboard.snapshot()["classes"] if p["id"] == keys[0])
            if item["state"] == "paused":
                break
            time.sleep(.02)
        assert item["state"] == "paused"
        assert item["checked_at"] != paused_time
        assert any(event["kind"] == "requested" for event in dashboard.snapshot()["activity"])
    finally:
        dashboard.shutdown()
    assert not dashboard._processes


def test_card_order_persists_without_restarting_and_survives_edit(dashboard):
    first, second, third = [add(dashboard, number) for number in (1, 2, 3)]
    with patch.object(dashboard, 'start') as start, patch.object(dashboard, 'pause') as pause:
        assert Api(dashboard).action('move_earlier', {'id':third}) == {'ok':True}
        start.assert_not_called()
        pause.assert_not_called()
    assert [p['id'] for p in dashboard.snapshot()['classes']] == [first, third, second]
    item = dashboard.snapshot()['classes'][0]
    dashboard.save_class(item)
    loaded = desktop_backend.Dashboard(dashboard.path)
    assert [p['id'] for p in loaded.snapshot()['classes']] == [first, third, second]
    loaded.move_class(first, 'earlier')
    loaded.move_class(second, 'later')
    assert [p['id'] for p in loaded.snapshot()['classes']] == [first, third, second]
    with pytest.raises(ValueError):
        loaded.move_class(first, 'invalid')


def test_theme_persists_and_rejects_unknown(dashboard):
    dashboard.set_theme('dark')
    assert desktop_backend.Dashboard(dashboard.path).snapshot()['theme'] == 'dark'
    with pytest.raises(ValueError):
        dashboard.set_theme('unknown')
