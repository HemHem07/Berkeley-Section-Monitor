"""One isolated monitor, with JSON events out and lifecycle commands in."""
from __future__ import annotations

import json
import logging
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


class InterruptibleClock:
    def __init__(self, stopped):
        self.stopped = stopped

    def monotonic(self):
        if self.stopped.is_set():
            raise KeyboardInterrupt
        return time.monotonic()

    def sleep(self, seconds):
        if self.stopped.wait(seconds):
            raise KeyboardInterrupt


def main():
    control, focus = WorkerControl(), threading.Event()
    stopped = control.stopped
    output_lock = threading.Lock()
    from lecture_context import LectureContext
    lecture = LectureContext(os.environ.get("COURSE_URL", ""), os.environ.get("DESKTOP_PARENT_ID", "")) if os.environ.get("SECTION_COMPONENT") == "DIS" else None
    status_received = False

    def emit(kind, **data):
        nonlocal status_received
        if kind == "status":
            status_received = True
        with output_lock:
            print(json.dumps({"kind": kind, **data}), flush=True)

    def refresh_lecture():
        nonlocal status_received
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
        if stopped.is_set():
            raise KeyboardInterrupt
        if focus.is_set():
            focus.clear()
            page.bring_to_front()

    class EventLog(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                # Exceptions can contain webhook URLs. Never send raw log text to HTML.
                message = record.getMessage().lower()
                notification = any(word in message for word in ("notification", "webhook", "email", "smtp"))
                emit("error", notification_error=notification, error=("Notification delivery failed. Check your .env notification settings; delivery will retry."
                                     if notification else "Check failed. Verify your connection and class settings; monitoring will retry."))

    monitor.report_event = emit
    monitor.login_tick = login_tick
    monitor.time = InterruptibleClock(stopped)
    monitor.wait_for_next_check = wait_for_next_check
    monitor.logger.addHandler(EventLog())
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
