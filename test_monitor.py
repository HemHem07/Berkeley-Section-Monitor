import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import monitor


def page(record: dict) -> str:
    settings = {"ucb": {"enrollment": {"available": record}}}
    return (
        '<script type="application/json" '
        'data-drupal-selector="drupal-settings-json">'
        f"{json.dumps(settings)}</script>"
    )


def record(code="C", description="Closed", enrolled=40, capacity=40):
    return {
        "id": 27743,
        "enrollmentStatus": {
            "status": {"code": code, "description": description},
            "enrolledCount": enrolled,
            "reservedCount": 0,
            "waitlistedCount": 0,
            "minEnroll": 0,
            "maxEnroll": capacity,
            "maxWaitlist": 6,
            "openReserved": 0,
        },
    }


def test_locates_and_determines_closed_section():
    found = monitor.locate_section(page(record()), "27743")
    status = monitor.determine_status(found)
    assert status.section_id == "27743"
    assert status.is_open is False
    assert status.enrolled == status.capacity == 40


def test_rejects_wrong_section_id():
    with pytest.raises(monitor.MonitorError, match="not requested ID"):
        monitor.locate_section(page(record()), "99999")


def test_unknown_code_falls_back_to_available_seats():
    status = monitor.determine_status(record("?", "Unknown", 39, 40))
    assert status.is_open is True


def test_parses_calcentral_enrollment_details():
    status = monitor.parse_calcentral_text(
        """
        Status Closed
        Enrollment Total 40
        Enrollment Capacity 40
        Wait List Total 1
        Wait List Capacity 6
        """,
        "27743",
    )
    assert status.section_id == "27743"
    assert status.is_open is False
    assert status.enrolled == status.capacity == 40
    assert status.waitlisted == 1
    assert status.waitlist_capacity == 6


def test_calcentral_parser_uses_available_seats_without_status():
    status = monitor.parse_calcentral_text(
        "Enrolled: 39 Capacity: 40 Waitlisted: 0 Waitlist Max: 6 "
        "Total Open Seats: 1",
        "27743",
    )
    assert status.is_open is True


def test_calcentral_parser_rejects_wrong_page():
    with pytest.raises(monitor.MonitorError, match="Enrollment Information"):
        monitor.parse_calcentral_text("My Academics", "27743")


def test_parses_people_soft_discussion_row():
    status = monitor.parse_calcentral_row(
        [
            "Select this row",
            "104 #27743\nWaitlist",
            "0",
            "40",
            "1 / 40",
            "In-Person Instruction",
        ],
        "27743",
        "104",
    )
    assert status.status_description == "Waitlist"
    assert status.is_open is False
    assert status.enrolled == 40
    assert status.waitlisted == 1
    assert status.waitlist_capacity == 40


def test_people_soft_open_row_is_open():
    status = monitor.parse_calcentral_row(
        ["Select this row", "104 #27743 Open", "2", "40", "0 / 40"],
        "27743",
        "104",
    )
    assert status.is_open is True
    assert status.enrolled == 38


def test_people_soft_row_rejects_wrong_discussion_number():
    with pytest.raises(monitor.MonitorError, match="Discussion 106"):
        monitor.parse_calcentral_row(
            ["Select this row", "104 #27743 Open", "2", "40", "0 / 40"],
            "27743",
            "106",
        )


@patch("monitor.send_notification")
@patch("monitor.fetch_page")
def test_notifies_once_on_closed_to_open(fetch, notify, tmp_path: Path):
    state = tmp_path / "state.json"
    fetch.return_value = page(record())
    monitor.run_check("https://example.test", "27743", state)
    notify.assert_not_called()

    fetch.return_value = page(record("O", "Open", 39, 40))
    monitor.run_check("https://example.test", "27743", state)
    notify.assert_called_once()


@patch("monitor.send_notification")
@patch("monitor.fetch_page")
def test_notifies_on_waitlist_change(fetch, notify, tmp_path: Path):
    state = tmp_path / "state.json"
    fetch.return_value = page(record())
    monitor.run_check("https://example.test", "27743", state)

    changed = record()
    changed["enrollmentStatus"]["waitlistedCount"] = 1
    fetch.return_value = page(changed)
    monitor.run_check("https://example.test", "27743", state)

    notify.assert_called_once()
    assert notify.call_args.args[2].waitlisted == 0

    monitor.run_check("https://example.test", "27743", state)
    notify.assert_called_once()


def test_missing_json_is_controlled_error():
    with pytest.raises(monitor.MonitorError, match="page may have changed"):
        monitor.locate_section("<html></html>")


def test_fetch_uses_curl_fallback_on_berkeley_403():
    class Response:
        status_code = 403

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    completed = subprocess.CompletedProcess([], 0, stdout="<html>ok</html>")
    with patch("monitor.requests.Session", return_value=Session()):
        with patch("monitor.subprocess.run", return_value=completed) as run:
            assert monitor.fetch_page("https://example.test") == "<html>ok</html>"
    run.assert_called_once()


@patch("monitor.time.sleep", side_effect=KeyboardInterrupt)
@patch("monitor.run_check")
def test_continuous_mode_stops_cleanly(run_check, sleep, tmp_path):
    result = monitor.run_continuously(
        "https://example.test", "27743", tmp_path / "state.json", 300
    )
    assert result == 0
    run_check.assert_called_once()
    sleep.assert_called_once_with(300)


def test_interval_must_be_reasonable():
    with pytest.raises(SystemExit):
        monitor.parse_args(["--continuous", "--interval", "5"])


@patch("monitor.send_notification")
def test_notification_command_sends_immediately(send):
    assert monitor.main(["--test-notification"]) == 0
    send.assert_called_once()


def test_test_notification_does_not_claim_section_is_open(monkeypatch):
    sent = {}

    class Response:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        sent.update(json)
        return Response()

    monkeypatch.setenv("NOTIFICATION_WEBHOOK_URL", "https://example.test/webhook")
    monkeypatch.setattr(monitor.requests, "post", fake_post)
    status = monitor.SectionStatus(
        "27743", "TEST", "Test notification", 39, 40, 0, 6, 0, True
    )
    monitor.send_notification(status, "https://example.test/class")

    assert sent["content"].startswith("TEST:")
    assert "does not indicate that the discussion section is open" in sent["content"]
    assert "39/40" not in sent["content"]


def test_discord_notification_pings_configured_user(monkeypatch):
    sent = {}

    class Response:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        sent.update(json)
        return Response()

    monkeypatch.setenv("NOTIFICATION_WEBHOOK_URL", "https://example.test/webhook")
    monkeypatch.setenv("DISCORD_USER_ID", "123456789012345678")
    monkeypatch.setattr(monitor.requests, "post", fake_post)
    current = monitor.determine_status(record("C", "Closed", 40, 40))
    previous = monitor.determine_status(record("C", "Closed", 39, 40))
    monitor.send_notification(
        current, "https://example.test/class", previous, ping=True
    )

    assert sent["content"].startswith("<@123456789012345678>")
    assert "Enrolled: 39 → 40" in sent["content"]


def test_unchanged_webhook_check_posts_without_ping(monkeypatch, tmp_path):
    sent = []

    class Response:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        sent.append(json["content"])
        return Response()

    monkeypatch.setenv("NOTIFICATION_WEBHOOK_URL", "https://example.test/webhook")
    monkeypatch.setenv("DISCORD_USER_ID", "123456789012345678")
    monkeypatch.setattr(monitor.requests, "post", fake_post)
    monkeypatch.setattr(monitor, "fetch_page", lambda *args: page(record()))
    state = tmp_path / "state.json"

    monitor.run_check("https://example.test/class", "27743", state)
    monitor.run_check("https://example.test/class", "27743", state)

    assert len(sent) == 2
    assert all(not message.startswith("<@") for message in sent)
    assert "checked — no change" in sent[-1]
    assert "Pacific Time" in sent[-1]
