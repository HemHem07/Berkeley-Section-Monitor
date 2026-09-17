"""HTML/CSS dashboard hosted in a local desktop window, with a Windows tray icon."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import webbrowser

from dotenv import load_dotenv
from desktop_backend import Dashboard, ROOT
from course_search import search_courses, course_sections


def dashboard_html():
    folder = ROOT / "desktop_ui"
    return (folder / "index.html").read_text(encoding="utf-8").replace(
        "/* APP_CSS */", (folder / "styles.css").read_text(encoding="utf-8")
    ).replace("/* APP_JS */", (folder / "app.js").read_text(encoding="utf-8"))


def simplify_title_bar(window):
    """Keep the native resize frame and caption buttons, without duplicate branding."""
    window.set_title("Berkeley Monitor")
    if os.name == "nt":
        import ctypes
        import io
        from System import Action
        from System.IO import MemoryStream
        from System.Drawing import Icon
        from System import Array, Byte
        buffer = io.BytesIO()
        application_icon().save(buffer, format="ICO", sizes=[(16,16),(32,32),(48,48),(64,64)])
        def apply():
            stream = MemoryStream(Array[Byte](buffer.getvalue()))
            original = Icon(stream)
            window.native.Icon = original.Clone()
            original.Dispose()
            stream.Dispose()
            window.native.ShowIcon = True
            class Options(ctypes.Structure):
                _fields_ = [("flags", ctypes.c_uint32), ("mask", ctypes.c_uint32)]
            options = Options(3, 3)  # Hide caption text and icon, retaining shell identity.
            configure = ctypes.windll.uxtheme.SetWindowThemeAttribute
            configure.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            configure.restype = ctypes.c_long
            result = configure(window.native.Handle.ToInt64(), 1, ctypes.byref(options), ctypes.sizeof(options))
            if result != 0:
                raise OSError("Windows could not simplify the title bar.")
        window.native.Invoke(Action(apply))


def application_icon():
    """Gold columns on navy, matching the dashboard's header mark."""
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=13, fill="#172f46")
    draw.rectangle((15, 13, 49, 17), fill="#f1c66e")
    draw.rectangle((15, 47, 49, 51), fill="#f1c66e")
    for x in (16, 26, 36, 46):
        draw.rectangle((x, 18, x + 3, 46), fill="#f1c66e")
    return image


class Api:
    def __init__(self, dashboard):
        self._dashboard = dashboard
        self._window = None
        self._tray = None
        self._tray_ready = threading.Event()
        self._quitting = threading.Event()

    def snapshot(self):
        result = self._dashboard.snapshot()
        result["tray_available"] = self._tray_ready.is_set()
        return result

    def action(self, name, values=None):
        values = values or {}
        try:
            if name == "search_courses":
                return {"ok": True, **search_courses(values.get("query"), values.get("term_id", ""), values.get("page", 0))}
            elif name == "course_sections":
                return {"ok": True, **course_sections(values.get("url"))}
            elif name == "save":
                self._dashboard.save_class(values)
            elif name in {"start", "pause", "remove", "focus_signin", "check_now"}:
                getattr(self._dashboard, name)(values["id"])
            elif name in {"start_all", "pause_all"}:
                getattr(self._dashboard, name)()
            elif name == "theme":
                self._dashboard.set_theme(values["theme"])
            elif name == "default":
                self._dashboard.set_default(values["policy"])
            elif name == "save_discord":
                self._dashboard.save_discord(values)
            elif name in {"move_earlier", "move_later"}:
                self._dashboard.move_class(values["id"], name.removeprefix("move_"), whole_group=values.get("whole_group") is True)
            elif name == "test_discord":
                self._dashboard.test_discord()
            elif name == "open_class":
                item = next(p for p in self._dashboard.snapshot()["classes"] if p["id"] == values["id"])
                if not item["url"].startswith("https://classes.berkeley.edu/content/"):
                    raise ValueError("Invalid Berkeley section URL.")
                webbrowser.open(item["url"])
            elif name == "find_class":
                webbrowser.open("https://classes.berkeley.edu/search/class")
            elif name == "hide":
                if not self._tray_ready.is_set():
                    raise ValueError("The tray is unavailable. Keep the dashboard open while monitoring.")
                self._hide()
            elif name == "quit":
                threading.Thread(target=self._quit, daemon=True).start()
            else:
                raise ValueError("Unknown dashboard action.")
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except (KeyError, StopIteration):
            return {"ok": False, "error": "This class is no longer on the dashboard. Refresh and try again."}
        except Exception:
            return {"ok": False, "error": "Could not complete that action. Check your connection and settings, then try again."}

    def _show(self, *_):
        self._window.show()
        self._window.restore()

    def _hide(self):
        if not self._dashboard.snapshot()["tray_notice_seen"]:
            try:
                self._tray.notify("Monitoring continues here. Double-click this icon to reopen; choose Quit to stop.", "Berkeley Monitor is still running")
            except Exception:
                pass
            self._dashboard.mark_tray_notice()
        self._window.hide()

    def _closing(self):
        if self._quitting.is_set():
            return True
        if self._tray_ready.is_set():
            self._hide()
        else:
            threading.Thread(target=self._quit, daemon=True).start()
        return False

    def _quit(self, *_):
        if self._quitting.is_set():
            return
        self._quitting.set()
        try:
            self._dashboard.shutdown()
        finally:
            if self._tray:
                self._tray.stop()
            self._window.destroy()

    def _start_tray(self):
        try:
            import pystray
            image = application_icon()
            self._tray = pystray.Icon("berkeley-monitor", image, "Berkeley Monitor", menu=pystray.Menu(
                pystray.MenuItem("Open dashboard", self._show, default=True),
                pystray.MenuItem("Start all", lambda: self.action("start_all")),
                pystray.MenuItem("Pause all", lambda: self.action("pause_all")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", lambda: threading.Thread(target=self._quit, daemon=True).start()),
            ))

            def ready(icon):
                icon.visible = True
                self._tray_ready.set()
                while not self._quitting.wait(2):
                    classes = self._dashboard.snapshot()["classes"]
                    running = sum(p["state"] in {"running", "starting"} for p in classes)
                    attention = sum(p["state"] in {"error", "signin", "stopped"} for p in classes)
                    icon.title = f"Berkeley Monitor · {running} running · {attention} need attention"

            self._tray.run(ready)
        except Exception:
            # Never hide an app that has no functioning tray icon.
            self._tray_ready.clear()


def main():
    load_dotenv(ROOT / ".env")
    os.chdir(ROOT)
    lock = open(ROOT / ".dashboard.lock", "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, "Berkeley Monitor is already running. Open it from the system tray.", "Berkeley Monitor", 0)
                return 0
        import webview
        if os.name == "nt":
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BerkeleySectionMonitor.Desktop")
        dashboard = Dashboard()
        api = Api(dashboard)
        window = webview.create_window("Berkeley Monitor", html=dashboard_html(), js_api=api,
                                       width=1180, height=820, min_size=(700, 540), background_color="#f7f8fa")
        api._window = window
        window.events.loaded += lambda: simplify_title_bar(window)
        window.events.closing += api._closing
        try:
            webview.start(lambda: threading.Thread(target=api._start_tray, daemon=True).start(),
                          gui="edgechromium" if os.name == "nt" else None, debug=False)
        finally:
            dashboard.shutdown()
            if api._tray:
                api._tray.stop()
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        message = ("The dashboard could not start. Install requirements.txt in .venv and ensure Microsoft Edge WebView2 Runtime is installed.\n\n"
                   + f"{type(exc).__name__}: {exc}")
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "Berkeley Monitor — startup error", 0x10)
        else:
            print(message, file=sys.stderr)
        sys.exit(1)
