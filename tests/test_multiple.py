import os
import subprocess
from unittest.mock import MagicMock, patch

import monitor
import setup_ui


def course(number, mode="public"):
    return dict(url=f"https://classes.berkeley.edu/content/class-{number}",
                label=f"Class {number}", section_id=str(number), component="LEC",
                number="001", term="2026 Fall", interval=30,
                continuous=True, mode=mode, parent="")


def test_multiple_isolates_settings_and_browser_profiles():
    profiles = [course(1, "calcentral"), course(2, "calcentral"), course(3)]
    children = [MagicMock(), MagicMock(), MagicMock()]
    for child in children:
        child.poll.return_value = 0
    before = dict(os.environ)
    with patch("monitor.subprocess.Popen", side_effect=children) as spawn:
        assert monitor.run_multiple(profiles) == 0
    calls = spawn.call_args_list
    environments = [call.kwargs["env"] for call in calls]
    assert [env["SECTION_ID"] for env in environments] == ["1", "2", "3"]
    assert len({env["STATE_FILE"] for env in environments}) == 3
    assert environments[0]["CALCENTRAL_PROFILE_DIR"] != environments[1]["CALCENTRAL_PROFILE_DIR"]
    assert "--calcentral" in calls[0].args[0]
    assert "--calcentral" not in calls[2].args[0]
    assert os.environ == before


def test_failed_class_does_not_stop_other_class():
    failed, running = MagicMock(), MagicMock()
    failed.poll.return_value = 1
    running.poll.side_effect = [None, 0]
    with patch("monitor.subprocess.Popen", side_effect=[failed, running]), patch("monitor.time.sleep"):
        assert monitor.run_multiple([course(1), course(2)]) == 1
    assert running.poll.call_count == 2
    running.terminate.assert_not_called()


def test_interrupt_cleans_up_children():
    child = MagicMock()
    child.poll.side_effect = KeyboardInterrupt
    child.wait.side_effect = [subprocess.TimeoutExpired("monitor", 5), 0]
    with patch("monitor.subprocess.Popen", return_value=child):
        assert monitor.run_multiple([course(1)]) == 0
    child.terminate.assert_called_once()


def test_picker_list_dispatches_without_single_class_check():
    profiles = [course(1), course(2)]
    with patch("setup_ui.choose_course", return_value=profiles), patch("monitor.run_multiple", return_value=0) as run, patch("monitor.run_check") as check:
        assert monitor.main(["--setup"]) == 0
    run.assert_called_once_with(profiles)
    check.assert_not_called()


def test_picker_adds_two_classes_and_starts_list(monkeypatch):
    import pytest
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No desktop display available")
    root.withdraw()
    monkeypatch.setattr(tk, "Tk", lambda: root)
    profiles = [course(1), course(2)]

    def step():
        widgets = root.winfo_children()[0].winfo_children()
        buttons = {w.cget("text"): w for w in widgets if w.winfo_class() == "TButton"}
        listing = next(w for w in widgets if w.winfo_class() == "Listbox")
        if listing.size() == 2:
            buttons["Start monitoring"].invoke()
            return
        add = buttons["Add to monitoring list"]
        if str(add.cget("state")) != "disabled":
            menu = next(w for w in widgets if w.winfo_class() == "TCombobox")
            menu.current(1 if listing.size() == 0 else 2)
            menu.event_generate("<<ComboboxSelected>>")
            add.invoke()
        root.after(50, step)

    root.after(20, step)
    root.after(5000, root.destroy)
    with patch("setup_ui.read_profiles", return_value=list(profiles)), patch("setup_ui.discover_course", side_effect=[dict(p) for p in profiles]), patch("setup_ui.save_profile"):
        result = setup_ui.choose_course()
    assert result == profiles
