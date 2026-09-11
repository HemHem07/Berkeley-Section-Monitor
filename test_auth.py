from unittest.mock import MagicMock, patch

import pytest

import monitor
from test_monitor import record


@pytest.mark.parametrize("message", [
    "BrowserContext.close: Connection closed while reading from the driver",
    "BrowserContext.close: Target page, context or browser has been closed",
])
def test_cleanup_tolerates_disconnected_driver_plain_exception(message):
    context = MagicMock()
    context.close.side_effect = Exception(message)
    monitor.close_browser_context(context)


def test_cleanup_does_not_hide_unrelated_errors():
    context = MagicMock()
    context.close.side_effect = RuntimeError("Unexpected protocol failure")
    with pytest.raises(RuntimeError, match="Unexpected protocol failure"):
        monitor.close_browser_context(context)


def test_startup_message_cannot_ping_even_with_mentions_in_label(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_WEBHOOK_URL", "https://example.test/webhook")
    monkeypatch.setenv("DISCORD_USER_ID", "12345")
    monkeypatch.setenv("COURSE_LABEL", "MATH 104 @everyone <@12345>")
    with patch("monitor.requests.post") as post:
        assert monitor.send_startup_notification("CalCentral", 60, True)
    payload = post.call_args.kwargs["json"]
    assert "Monitor started:" in payload["content"]
    assert "60 seconds" in payload["content"]
    assert payload["allowed_mentions"] == {"parse": [], "users": [], "roles": [], "replied_user": False}


def test_startup_announced_once_after_success_and_retried_after_delivery_failure(tmp_path):
    with patch("monitor.run_check", side_effect=[monitor.MonitorError("offline"), None, None, None]), \
         patch("monitor.send_startup_notification", side_effect=[False, True]) as startup, \
         patch("monitor.time.sleep", side_effect=[None, None, None, KeyboardInterrupt]):
        assert monitor.run_continuously("https://example.test", "12345", tmp_path / "state.json", 60) == 0
    assert startup.call_count == 2


def test_signin_notification_mentions_only_configured_user(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_WEBHOOK_URL", "https://example.test/webhook")
    monkeypatch.setenv("DISCORD_USER_ID", "12345")
    with patch("monitor.requests.post") as post:
        monitor.send_signin_notification()
    payload = post.call_args.kwargs["json"]
    assert payload["content"].startswith("<@12345> CalCentral sign-in required")
    assert "paused" in payload["content"]
    assert payload["allowed_mentions"] == {"parse": [], "users": ["12345"]}


def test_auth_detection_does_not_confuse_a_network_error():
    page = MagicMock()
    page.url = "https://bcsweb.is.berkeley.edu/search"
    page.frames = []
    page.get_by_role.return_value.count.return_value = 0
    page.get_by_text.return_value.count.return_value = 0
    monitor.require_signed_in(page)
    page.url = "https://auth.berkeley.edu/cas/login"
    with pytest.raises(monitor.SignInRequired):
        monitor.require_signed_in(page)


def test_auth_detection_in_iframe():
    page = MagicMock()
    page.url = "https://bcsweb.is.berkeley.edu/search"
    page.get_by_role.return_value.count.return_value = 0
    page.get_by_text.return_value.count.return_value = 0
    frame = MagicMock()
    frame.url = "https://auth.berkeley.edu/cas/login"
    page.frames = [frame]
    with pytest.raises(monitor.SignInRequired):
        monitor.require_signed_in(page)


def test_lecture_popup_matches_observed_calcentral_layout():
    text = (
        "MATH (Mathematics) 104: Introduction To Analysis Waitlist "
        "LEC 009 #26088 / Regular Academic Session / 4.00 units "
        "2026 Fall Seat Availability Open: 0 Capacity: 45 Waitlisted: 0 / 3"
    )
    result = monitor.parse_calcentral_card(text, "26088")
    assert (result.enrolled, result.capacity, result.waitlisted, result.waitlist_capacity) == (45, 45, 0, 3)
    assert result.status_description == "Waitlist"
    with pytest.raises(monitor.MonitorError):
        monitor.parse_calcentral_card(text, "99999")


def test_headless_recovery_pings_once_per_incident(tmp_path):
    status = monitor.determine_status(record())
    browser = MagicMock()
    context = browser.chromium.launch_persistent_context.return_value
    context.pages = [MagicMock()]
    with patch("playwright.sync_api.sync_playwright") as runtime, \
         patch("monitor.fetch_calcentral_status", side_effect=[
             monitor.SignInRequired(), monitor.SignInRequired(), status,
             monitor.SignInRequired(), status,
         ]), patch("monitor.require_signed_in"), patch("monitor.wait_for_enrollment_entry"), patch("monitor.complete_calcentral_login"), \
         patch("monitor.send_signin_notification") as notify, \
         patch("monitor.process_status"), patch("builtins.input", return_value=""), \
         patch("monitor.time.sleep", side_effect=[None, KeyboardInterrupt]):
        runtime.return_value.__enter__.return_value = browser
        with pytest.raises(KeyboardInterrupt):
            monitor.run_calcentral("27743", "104", tmp_path / "state.json", 30, True)
    assert notify.call_count == 2
    assert [c.kwargs["headless"] for c in browser.chromium.launch_persistent_context.call_args_list] == [
        True, False, True, False,
    ]
    context.close.assert_called()


def test_session_cookies_transferred_before_headless_navigation(tmp_path):
    initial, visible, resumed = MagicMock(), MagicMock(), MagicMock()
    for context in (initial, visible, resumed):
        context.pages = [MagicMock()]
    initial.cookies.return_value = []
    cookies = [{"name": "session", "value": "fake-test-token", "domain": "example.test",
                "path": "/", "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Lax"}]
    visible.cookies.return_value = cookies
    status = monitor.determine_status(record())
    browser = MagicMock()
    browser.chromium.launch_persistent_context.side_effect = [initial, visible, resumed]

    def fetch(page, **kwargs):
        if page is initial.pages[0]:
            raise monitor.SignInRequired()
        resumed.add_cookies.assert_called_once_with(cookies)
        return status

    with patch("playwright.sync_api.sync_playwright") as runtime, \
         patch("monitor.fetch_calcentral_status", side_effect=fetch), \
         patch("monitor.require_signed_in"), patch("monitor.wait_for_enrollment_entry"), patch("monitor.complete_calcentral_login"), patch("monitor.send_signin_notification"), \
         patch("monitor.process_status"), patch("builtins.input", return_value=""):
        runtime.return_value.__enter__.return_value = browser
        assert monitor.run_calcentral("27743", "104", tmp_path / "state.json", 30, False) == 0
    visible.cookies.assert_called_once()
    visible.close.assert_called_once()
    resumed.close.assert_called_once()


def test_hidden_expired_message_does_not_force_signin():
    page = MagicMock()
    page.url = "https://calcentral.berkeley.edu/academics"
    page.frames = []
    page.get_by_role.return_value.count.return_value = 1
    page.get_by_role.return_value.first.is_visible.return_value = False
    page.get_by_text.return_value.count.return_value = 1
    page.get_by_text.return_value.first.is_visible.return_value = False
    monitor.require_signed_in(page)


def test_failed_alert_retries_and_does_not_change_enrollment_state(tmp_path):
    status = monitor.determine_status(record())
    browser = MagicMock()
    browser.chromium.launch_persistent_context.return_value.pages = [MagicMock()]
    with patch("playwright.sync_api.sync_playwright") as runtime, \
         patch("monitor.fetch_calcentral_status", side_effect=[monitor.SignInRequired(), monitor.SignInRequired(), status]), \
         patch("monitor.require_signed_in"), patch("monitor.wait_for_enrollment_entry"), patch("monitor.complete_calcentral_login"), \
         patch("monitor.send_signin_notification", side_effect=[monitor.MonitorError("delivery failed"), None]) as notify, \
         patch("monitor.process_status") as process, patch("builtins.input", return_value=""), \
         patch("monitor.time.sleep", side_effect=[None, KeyboardInterrupt]):
        runtime.return_value.__enter__.return_value = browser
        with pytest.raises(KeyboardInterrupt):
            monitor.run_calcentral("27743", "104", tmp_path / "state.json", 30, True)
    assert notify.call_count == 2
    process.assert_called_once()
