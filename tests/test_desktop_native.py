"""Opt-in native webview/tray smoke test, isolated from real classes and notifications."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.getenv("RUN_DESKTOP_NATIVE_TESTS") != "1", reason="Opt-in Windows native GUI QA")
def test_native_bridge_and_tray(tmp_path):
    result = subprocess.run([sys.executable, "-m", "tests.test_desktop_native", "--probe", str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "native-ok.txt").read_text() == "bridge, tray, hide, quit verified"


def probe(folder):
    import threading
    from unittest.mock import patch
    import webview
    from desktop import Api, dashboard_html, simplify_title_bar
    from desktop_backend import Dashboard

    with patch("desktop_backend.read_profiles", return_value=[]):
        dashboard = Dashboard(Path(folder) / "dashboard.json")
    api = Api(dashboard)
    window = webview.create_window("Berkeley Monitor QA", html=dashboard_html(), js_api=api)
    api._window = window
    window.events.closing += api._closing
    errors = []

    def check():
        try:
            assert window.events.loaded.wait(10), "Webview did not load"
            simplify_title_bar(window)
            assert window.native.Text == "Berkeley Monitor"
            assert window.native.Icon is not None
            assert window.native.MinimizeBox and window.native.MaximizeBox
            assert str(window.native.FormBorderStyle) == "Sizable"
            received = threading.Event()
            results = []
            def result_ready(result):
                results.append(result)
                received.set()
            window.evaluate_js("window.pywebview.api.snapshot()", callback=result_ready)
            assert received.wait(5), "Bridge did not respond"
            assert results[0]["classes"] == []
            threading.Thread(target=api._start_tray, daemon=True).start()
            assert api._tray_ready.wait(8), "Tray icon did not initialize"
            window.destroy()  # Exercise the real native close event, which must hide.
            assert not window.events.closed.is_set()
            assert dashboard.snapshot()["tray_notice_seen"]
            api._show()
            (Path(folder) / "native-ok.txt").write_text("bridge, tray, hide, quit verified")
        except Exception as exc:
            errors.append(repr(exc))
        finally:
            api._quit()

    webview.start(check, gui="edgechromium")
    assert not errors, errors


if __name__ == "__main__" and "--probe" in sys.argv:
    probe(sys.argv[-1])
