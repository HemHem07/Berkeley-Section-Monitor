"""One isolated monitor, with JSON events out and lifecycle commands in."""
from __future__ import annotations

import json
import os
import sys
import threading
import time

import monitor


class WorkerControl:
    """Wake only the scheduled delay; never overlap an in-flight browser check."""
    def __init__(self):
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.waiting = False

    def stop(self):
        self.stopped.set()
        self.wake.set()

    def checkpoint(self):
        if self.stopped.is_set():
            raise KeyboardInterrupt

    def check_now(self):
        with self.lock:
            if self.waiting:
                self.wake.set()

    def wait(self, seconds, emit):
        with self.lock:
            if self.stopped.is_set():
                raise KeyboardInterrupt
            self.wake.clear()
            self.waiting = True
        try:
            emit("waiting", next_check_at=time.time() + seconds)
            self.wake.wait(seconds)
        finally:
            with self.lock:
                self.waiting = False
        if self.stopped.is_set():
            raise KeyboardInterrupt


def main():
    control, focus = WorkerControl(), threading.Event()
    stopped = control.stopped
    output_lock = threading.Lock()
    from lecture_context import LectureContext
    lecture = LectureContext(os.environ.get("COURSE_URL", ""), os.environ.get("DESKTOP_PARENT_ID", "")) if os.environ.get("SECTION_COMPONENT") == "DIS" else None
    status_received = False
    meeting_loaded = False

    def emit(kind, **data):
        nonlocal status_received
        if kind == "status":
            status_received = True
        with output_lock:
            print(json.dumps({"kind": kind, **data}), flush=True)

    def refresh_lecture():
        nonlocal status_received, meeting_loaded
        if status_received and not meeting_loaded and not stopped.is_set():
            from courses import meeting_details
            try:
                html = monitor.fetch_page(os.environ["COURSE_URL"])
                monitor.locate_section(html, os.environ["SECTION_ID"])
                emit("meeting", meeting=meeting_details(html))
                meeting_loaded = True
            except (monitor.MonitorError, ValueError, OSError):
                pass  # Retry after the next successful enrollment check.
        if lecture is not None and status_received and not stopped.is_set():
            status_received = False
            lecture.refresh(emit)

    def wait_for_next_check(seconds):
        refresh_lecture()
        control.wait(seconds, emit)

    def commands():
        try:
            for line in sys.stdin:
                if line.strip() == "focus":
                    focus.set()
                elif line.strip() == "check_now":
                    control.check_now()
                elif line.strip() == "stop":
                    break
        finally:
            # Parent exit also closes stdin: never leave an orphan monitor running.
            control.stop()

    def login_tick(page):
        control.checkpoint()
        if focus.is_set():
            focus.clear()
            page.bring_to_front()

    monitor.report_event = emit
    monitor.login_tick = login_tick
    monitor.cancellation_checkpoint = control.checkpoint
    monitor.wait_for_next_check = wait_for_next_check
    threading.Thread(target=commands, daemon=True).start()
    args = ["--no-ui", "--interval", os.environ["CHECK_INTERVAL_SECONDS"]]
    if os.environ.get("DESKTOP_ONCE") != "1":
        args.append("--continuous")
    else:
        emit("checking")
    if os.environ.get("DESKTOP_SOURCE") == "calcentral":
        args.append("--calcentral")
    try:
        result = monitor.main(args)
        refresh_lecture()
        return result
    except KeyboardInterrupt:
        return 0
    except Exception:
        emit("error", error="Monitor stopped unexpectedly. Review the class settings and start it again.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
