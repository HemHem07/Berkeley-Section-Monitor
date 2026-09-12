"""Persistent desktop settings and independently controlled monitor processes."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse

from setup_ui import discover_course, profile_environment, read_profiles
from monitor import SectionStatus, describe_changes
import discord_settings

ROOT = Path(__file__).resolve().parent
POLICIES = {"seats", "waitlist", "changes", "muted"}
ACTIVE = {"starting", "running", "signin", "error", "stopping"}


def class_id(url):
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def notification_configuration():
    webhook = os.getenv("NOTIFICATION_WEBHOOK_URL", "").strip()
    if webhook:
        host = (urlparse(webhook).hostname or "").lower()
        channel = "Discord" if host in {"discord.com", "discordapp.com"} or host.endswith(".discord.com") else "Webhook"
    else:
        channel = "Email" if os.getenv("SMTP_HOST") else "Not configured"
    return {"channel":channel, "configured":channel != "Not configured",
            "mentions":channel == "Discord" and os.getenv("DISCORD_USER_ID", "").strip().isdigit(),
            "user_id":os.getenv("DISCORD_USER_ID", "").strip()}


class Dashboard:
    def __init__(self, path=None, process_factory=None):
        self.path = Path(path) if path else ROOT / ".dashboard.json"
        self._spawn = process_factory or subprocess.Popen
        self._lock = threading.RLock()
        self._actions = threading.RLock()
        self._processes = {}
        self._quitting = False
        self.data = {"version": 1, "default_notification": "seats", "tray_notice_seen": False, "classes": [], "activity": []}
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or loaded.get("version") != 1 or not isinstance(loaded.get("classes"), list):
                raise ValueError("Cannot read dashboard settings. Keep .dashboard.json for recovery.")
            if loaded.get("default_notification") not in POLICIES:
                raise ValueError("Invalid default notification setting in .dashboard.json.")
            self.data.update(loaded)
        else:
            for profile in read_profiles():
                self.data["classes"].append({**profile, "id": class_id(profile["url"]), "notification": "default"})
        for item in self.data["classes"]:
            item["id"] = class_id(item["url"])
            item["state"] = "paused"
            item["error"] = ""
            item.update(check_phase="idle", next_check_at=None)
        self._save()

    def _activity(self, item, kind, message):
        """Bounded local history containing class data, never raw logs or credentials."""
        self.data["activity"].insert(0, {
            "at": datetime.now(timezone.utc).isoformat(), "class_id": item["id"],
            "label": item["label"], "kind": kind, "message": message,
        })
        del self.data["activity"][200:]

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def _find(self, key):
        return next(item for item in self.data["classes"] if item["id"] == key)

    def snapshot(self):
        with self._lock:
            result = copy.deepcopy(self.data)
            result["notification_configured"] = bool(os.getenv("NOTIFICATION_WEBHOOK_URL") or os.getenv("SMTP_HOST"))
            result["notifications"] = notification_configuration()
            result["quitting"] = self._quitting
            return result

    def save_class(self, values):
        mode = values.get("mode", "public")
        notification = values.get("notification", "default")
        interval = int(values.get("interval", 60))
        if mode not in {"public", "calcentral"} or notification not in POLICIES | {"default"}:
            raise ValueError("Choose a valid source and notification preference.")
        if not 30 <= interval <= 86400:
            raise ValueError("Choose a check interval between 30 and 86400 seconds.")
        with self._actions:
            if self._quitting:
                raise ValueError("The app is shutting down.")
            with self._lock:
                old = copy.deepcopy(self._find(values["id"])) if values.get("id") else None
            url = str(values.get("url", "")).strip()
            if old and url != old["url"]:
                raise ValueError("Add a new card to monitor a different section URL.")
            # Editing interval/alerts works offline; adding a class resolves its identity.
            profile = old or discover_course(url)
            parent = str(values.get("parent", "")).strip()
            if mode == "calcentral" and profile["component"] == "DIS" and not parent.isdigit():
                raise ValueError("CalCentral discussions need the parent lecture class number.")
            key = class_id(profile["url"])
            with self._lock:
                if not old and any(p["id"] == key for p in self.data["classes"]):
                    raise ValueError("That class is already on your dashboard. Use its Settings button.")
                was_running = key in self._processes
            if was_running:
                self.pause(key)
            profile.update(id=key, mode=mode, parent=parent, interval=interval,
                           continuous=True, notification=notification, state="paused", error="")
            with self._lock:
                if old:
                    self.data["classes"] = [profile if p["id"] == key else p for p in self.data["classes"]]
                else:
                    self.data["classes"].append(profile)
                self._activity(profile, "settings", "Class settings updated." if old else "Class added to dashboard.")
                self._save()
            if was_running:
                self.start(key)
            return key

    def save_discord(self, values):
        with self._actions:
            if self._quitting:
                raise ValueError("The app is shutting down.")
            webhook = str(values.get("webhook", "")).strip() or os.getenv("NOTIFICATION_WEBHOOK_URL", "").strip()
            user_id = str(values.get("user_id", "")).strip()
            discord_settings.save(ROOT / ".env", webhook, user_id)
            os.environ.update(NOTIFICATION_WEBHOOK_URL=webhook, DISCORD_USER_ID=user_id)
            with self._lock:
                running = [(key, self._find(key).get("run_once", False)) for key in self._processes]
            for key, once in running:
                self.pause(key)
                self.start(key, once=once)

    def test_discord(self):
        with self._actions:
            discord_settings.send_test(os.getenv("NOTIFICATION_WEBHOOK_URL", "").strip(),
                                       os.getenv("DISCORD_USER_ID", "").strip())

    def start(self, key, *, once=False):
        with self._actions, self._lock:
            if self._quitting:
                raise ValueError("The app is shutting down.")
            if key in self._processes:
                return
            item = self._find(key)
            env = os.environ.copy()
            env.update(profile_environment(item))
            env["NOTIFICATION_MODE"] = (self.data["default_notification"] if item.get("notification", "default") == "default"
                                        else item["notification"])
            env["DESKTOP_SOURCE"] = item["mode"]
            env["DESKTOP_PARENT_ID"] = item.get("parent", "")
            env["DESKTOP_ONCE"] = "1" if once else "0"
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["CALCENTRAL_PROFILE_DIR"] = str(ROOT / ".calcentral-browser-profile" / "classes" / f"{key}-calcentral")
            python = Path(sys.executable)
            if python.name.lower() == "pythonw.exe":
                python = python.with_name("python.exe")
            process = self._spawn([str(python), str(ROOT / "desktop_worker.py")], cwd=ROOT, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self._processes[key] = process
            item.update(state="starting", error="", check_phase="checking", next_check_at=None, run_once=once)
            self._activity(item, "started", "One check requested; will return to paused." if once else "Monitoring started.")
            self._save()
            threading.Thread(target=self._read_events, args=(key, process), daemon=True).start()

    def check_now(self, key):
        with self._actions, self._lock:
            item = self._find(key)
            process = self._processes.get(key)
            if not process:
                self.start(key, once=True)
                return
            if item["state"] in {"signin", "stopping"} or item.get("check_phase") != "waiting":
                return
            process.stdin.write("check_now\n")
            process.stdin.flush()
            item.update(check_phase="checking", next_check_at=None)
            self._activity(item, "requested", "Check now requested.")
            self._save()

    def _read_events(self, key, process):
        try:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                with self._lock:
                    if self._processes.get(key) is not process:
                        return
                    item = self._find(key)
                    if item["state"] == "stopping":
                        continue
                    if event.get("kind") == "status":
                        previous = item.get("status")
                        changes = describe_changes(SectionStatus(**previous), SectionStatus(**event["status"])) if previous else []
                        self._activity(item, "changed" if changes else "checked",
                                       "; ".join(changes) if changes else "Check succeeded. No change." if previous else "First check succeeded; availability recorded.")
                        item.update(status=event["status"], checked_at=event["checked_at"], state="running", error="")
                    elif event.get("kind") == "checking":
                        item.update(check_phase="checking", next_check_at=None)
                        if item["state"] == "signin":
                            item.update(state="starting", error="")
                    elif event.get("kind") == "waiting":
                        item.update(check_phase="waiting", next_check_at=event["next_check_at"])
                    elif event.get("kind") == "signin":
                        self._activity(item, "signin", "CalNet / Duo sign-in needed.")
                        item.update(state="signin", error="Complete CalNet and Duo in the monitor’s Chrome window.", check_phase="idle", next_check_at=None)
                    elif event.get("kind") == "error":
                        if event.get("notification_error"):
                            item["notification_delivery"] = {"state":"failed", "at":datetime.now(timezone.utc).isoformat()}
                        self._activity(item, "error", event["error"])
                        item.update(state="error", error=event["error"])
                    elif event.get("kind") == "notified":
                        self._activity(item, "notified", "Enrollment notification delivered.")
                        item["notification_delivery"] = {"state":"delivered", "at":datetime.now(timezone.utc).isoformat()}
                    elif event.get("kind") == "lecture":
                        item.update(lecture=event["lecture"], lecture_error="")
                    elif event.get("kind") == "lecture_error":
                        item["lecture_error"] = event["error"]
                    self._save()
        finally:
            code = process.wait()
            with self._lock:
                if self._processes.get(key) is process:
                    self._processes.pop(key)
                    item = self._find(key)
                    if item["state"] == "stopping":
                        return  # pause() records the final state and lifecycle entry.
                    if item.get("run_once") and code == 0:
                        item.update(state="paused", error="", check_phase="idle", next_check_at=None)
                        self._activity(item, "paused", "One check completed. Monitoring remains paused.")
                    else:
                        item.update(state="stopped", error=item.get("error") or "Monitor stopped. Start it again to resume checks.", check_phase="idle", next_check_at=None)
                        self._activity(item, "stopped", "Monitor stopped.")
                    self._save()

    def pause(self, key):
        with self._actions:
            with self._lock:
                item = self._find(key)
                process = self._processes.get(key)
                if process:
                    item["state"] = "stopping"
                    item.update(check_phase="idle", next_check_at=None)
            if process:
                try:
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                except (BrokenPipeError, OSError, ValueError):
                    pass
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
                    else:
                        process.terminate()
                    process.wait(timeout=10)
                with self._lock:
                    self._processes.pop(key, None)
                process.stdin.close()
            with self._lock:
                item.update(state="paused", error="", check_phase="idle", next_check_at=None)
                if process:
                    self._activity(item, "paused", "Monitoring paused.")
                self._save()

    def remove(self, key):
        with self._actions:
            self.pause(key)
            with self._lock:
                self.data["classes"] = [p for p in self.data["classes"] if p["id"] != key]
                # Retain history after removal so past alerts remain explainable.
                self._save()

    def start_all(self):
        for item in self.snapshot()["classes"]:
            self.start(item["id"])

    def move_class(self, key, direction):
        if direction not in {"earlier", "later"}:
            raise ValueError("Choose a valid card direction.")
        with self._actions, self._lock:
            items = self.data["classes"]
            item = self._find(key)
            index = items.index(item)
            destination = index + (-1 if direction == "earlier" else 1)
            if 0 <= destination < len(items):
                items[index], items[destination] = items[destination], items[index]
                self._save()

    def pause_all(self):
        for item in self.snapshot()["classes"]:
            self.pause(item["id"])

    def set_theme(self, theme):
        if theme not in {"light", "dark", "system"}:
            raise ValueError("Choose light, dark, or system appearance.")
        with self._lock:
            self.data["theme"] = theme
            self._save()

    def set_default(self, policy):
        if policy not in POLICIES:
            raise ValueError("Choose a valid notification preference.")
        with self._actions:
            if self.data["default_notification"] == policy:
                return
            with self._lock:
                affected = [p["id"] for p in self.data["classes"] if p.get("notification", "default") == "default" and p["id"] in self._processes]
                self.data["default_notification"] = policy
                self._save()
            for key in affected:
                self.pause(key)
                self.start(key)

    def focus_signin(self, key):
        with self._lock:
            process = self._processes.get(key)
            if not process or self._find(key)["state"] != "signin":
                raise ValueError("This class is not currently waiting for sign-in.")
            process.stdin.write("focus\n")
            process.stdin.flush()

    def mark_tray_notice(self):
        with self._lock:
            self.data["tray_notice_seen"] = True
            self._save()

    def shutdown(self):
        with self._actions:
            self._quitting = True
            self.pause_all()
