import os
from unittest.mock import MagicMock, patch

import pytest

import monitor
import setup_ui
from test_monitor import page, record

LECTURE_URL = "https://classes.berkeley.edu/content/2026-fall-math-104-009-lec-009"


@pytest.mark.parametrize("component", ["lec", "dis"])
def test_discovery_reads_identity_from_berkeley(component):
    url = LECTURE_URL.replace("lec", component)
    with patch("setup_ui.fetch_page", return_value=page(record())):
        profile = setup_ui.discover_course(url)
    assert profile["section_id"] == "27743"
    assert profile["component"] == component.upper()
    assert profile["number"] == "009"
    assert profile["term"] == "2026 Fall"


@pytest.mark.parametrize("url", ["https://example.com/content/class", "https://classes.berkeley.edu/search/class"])
def test_discovery_rejects_non_section_urls(url):
    with pytest.raises(ValueError):
        setup_ui.discover_course(url)


def test_profiles_remember_choices_and_isolate_state(tmp_path, monkeypatch):
    path = tmp_path / "profiles.json"
    with patch("setup_ui.fetch_page", return_value=page(record())):
        profile = setup_ui.discover_course(LECTURE_URL)
    profile.update(mode="public", interval=30, continuous=True, parent="")
    setup_ui.save_profile(profile, path)
    profile["interval"] = 60
    setup_ui.save_profile(profile, path)
    assert setup_ui.read_profiles(path) == [profile]
    monkeypatch.setattr(setup_ui, "PROFILE_PATH", path)
    with patch.dict(os.environ, {"SECTION_ID": "old", "STATE_FILE": "old.json"}):
        setup_ui.apply_profile(profile)
        assert os.environ["SECTION_ID"] == "27743"
        assert os.environ["SECTION_COMPONENT"] == "LEC"
        public_state = os.environ["STATE_FILE"]
        profile["mode"] = "calcentral"
        setup_ui.apply_profile(profile)
        assert os.environ["STATE_FILE"] != public_state


def test_unknown_waitlist_is_not_enrollment_capacity():
    status = monitor.parse_calcentral_row(
        ["Select", "104 #27743 Waitlist", "0", "40", "1 / 40"], "27743", "104"
    )
    assert status.waitlisted == 1
    assert status.waitlist_capacity is None
    data = record()
    del data["enrollmentStatus"]["maxWaitlist"]
    assert monitor.determine_status(data).waitlist_capacity is None


def test_capacity_refresh_recovers_after_failure():
    with patch("monitor.fetch_page", side_effect=[monitor.MonitorError("offline"), page(record())]):
        assert monitor.fetch_waitlist_capacity(LECTURE_URL, "27743") is None
        assert monitor.fetch_waitlist_capacity(LECTURE_URL, "27743") == 6


def test_lecture_details_require_matching_identity():
    browser = MagicMock()
    browser.frames = [MagicMock()]
    body = browser.frames[0].locator.return_value
    body.inner_text.return_value = (
        "Class Number: 26088 Enrollment Total 30 Enrollment Capacity 40 "
        "Wait List Total 2 Wait List Capacity 5 Status Open"
    )
    status = monitor.fetch_calcentral_detail(browser, "26088")
    assert status.enrolled == 30
    assert status.waitlist_capacity == 5
    browser.reload.assert_called_once()
    with patch("monitor.time.monotonic", side_effect=[0, 1, 31]):
        with pytest.raises(monitor.MonitorError, match="#99999"):
            monitor.fetch_calcentral_detail(browser, "99999")


def test_default_interval_and_skip_ui(monkeypatch):
    monkeypatch.delenv("CHECK_INTERVAL_SECONDS", raising=False)
    assert monitor.parse_args([]).interval == 60
    assert monitor.parse_args(["--interval", "30"]).interval == 30
    with patch("monitor.sys.stdin.isatty", return_value=True), patch("monitor.run_check"), patch("setup_ui.choose_course") as chooser:
        assert monitor.main(["--no-ui"]) == 0
        chooser.assert_not_called()


def test_cancel_picker_does_not_start_monitoring():
    with patch("setup_ui.choose_course", return_value=None), patch("monitor.run_check") as check:
        assert monitor.main(["--setup"]) == 0
        check.assert_not_called()


def test_env_loaded_without_replacing_exported_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "__file__", str(tmp_path / "monitor.py"))
    (tmp_path / ".env").write_text('COURSE_LABEL="MATH 104 lecture"\nSECTION_ID=26088\n')
    monkeypatch.setenv("SECTION_ID", "12345")
    monkeypatch.delenv("COURSE_LABEL", raising=False)
    with patch("monitor.run_check"):
        assert monitor.main(["--no-ui"]) == 0
    assert os.environ["COURSE_LABEL"] == "MATH 104 lecture"
    assert os.environ["SECTION_ID"] == "12345"


def test_desktop_picker_starts_saved_course(monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No desktop display available")
    root.withdraw()
    monkeypatch.setattr(tk, "Tk", lambda: root)
    with patch("setup_ui.fetch_page", return_value=page(record())):
        profile = setup_ui.discover_course(LECTURE_URL)
    profile.update(mode="public", interval=30, continuous=True, parent="")

    def click_start():
        for widget in root.winfo_children()[0].winfo_children():
            if widget.winfo_class() == "TButton" and widget.cget("text") == "Start monitoring":
                widget.invoke()
                return
        raise AssertionError("Start button missing")

    root.after(20, click_start)
    root.after(3000, root.destroy)
    with patch("setup_ui.read_profiles", return_value=[profile]), patch("setup_ui.discover_course", return_value=dict(profile)), patch("setup_ui.save_profile") as save:
        result = setup_ui.choose_course()
    assert result == profile
    save.assert_called_once_with(profile)
