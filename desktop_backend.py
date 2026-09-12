"""Persistent desktop settings and independently controlled monitor processes."""
from __future__ import annotations

import copy
import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse

from courses import class_id, discover_course, profile_environment, read_profiles, validate_profile
from monitor import SectionStatus, describe_changes
import discord_settings

ROOT = Path(__file__).resolve().parent
POLICIES = {"seats", "waitlist", "changes", "muted"}


def course_group_key(item):
    match = re.fullmatch(r"https://classes\.berkeley\.edu/content/(\d{4}-(?:fall|spring|summer)-.+)-\d+-dis-\d+/?", item["url"], re.I) if item.get("component") == "DIS" else None
    return match[1].lower() if match else item["id"]


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


def validate_status(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid saved availability.")
    for name in ("section_id", "status_code", "status_description"):
        if not isinstance(value.get(name), str):
            raise ValueError("Invalid saved availability.")
    for name in ("enrolled", "capacity", "waitlisted", "open_reserved", "waitlist_capacity"):
        count = value.get(name)
        if name == "waitlist_capacity" and name in value and count is None:
            continue
        if type(count) is not int or count < 0:
            raise ValueError("Invalid saved availability.")
    if type(value.get("is_open")) is not bool:
        raise ValueError("Invalid saved availability.")


def status_record(value):
    return SectionStatus(**{name: value[name] for name in SectionStatus.__dataclass_fields__})


def validate_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Invalid saved timestamp.")
    datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_lecture(value):
    if not isinstance(value, dict) or any(not isinstance(value.get(k), str) for k in ("url", "label", "section_id", "source")):
        raise ValueError("Invalid saved lecture.")
    validate_status(value.get("status"))
    validate_timestamp(value.get("checked_at"))


def validate_dashboard(data):
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1 or not isinstance(data.get("classes"), list):
        raise ValueError("Cannot read dashboard settings. Keep .dashboard.json for recovery.")
    if data.get("default_notification") not in ("seats", "waitlist", "changes", "muted"):
        raise ValueError("Invalid default notification setting in .dashboard.json.")
    if data.get("theme", "system") not in ("system", "light", "dark") or type(data.get("tray_notice_seen", False)) is not bool:
        raise ValueError("Invalid saved preferences.")
    seen = set()
    for item in data["classes"]:
        validate_profile(item)
        if item["url"] in seen:
            raise ValueError("Duplicate saved class URL.")
        seen.add(item["url"])
        if item.get("notification", "default") not in ("default", "seats", "waitlist", "changes", "muted"):
            raise ValueError("Invalid saved notification preference.")
        for field in ("state", "error", "check_phase", "lecture_error"):
            if field in item and not isinstance(item[field], str):
                raise ValueError("Invalid saved class state.")
        if item.get("status") is not None:
            validate_status(item["status"])
        if item.get("checked_at") is not None:
            validate_timestamp(item["checked_at"])
        if item.get("lecture") is not None:
            validate_lecture(item["lecture"])
        if item.get("notification_delivery") is not None:
            delivery = item["notification_delivery"]
            if not isinstance(delivery, dict) or delivery.get("state") not in ("failed", "delivered"):
                raise ValueError("Invalid saved delivery result.")
            validate_timestamp(delivery.get("at"))
    activity = data.get("activity", [])
    if not isinstance(activity, list):
        raise ValueError("Invalid saved activity.")
    for event in activity:
        if not isinstance(event, dict) or any(not isinstance(event.get(k), str) for k in ("at", "class_id", "label", "kind", "message")):
            raise ValueError("Invalid saved activity entry.")
        validate_timestamp(event["at"])


def validate_event(event):
    if not isinstance(event, dict) or not isinstance(event.get("kind"), str):
        raise ValueError("Invalid worker event.")
    kind = event["kind"]
    if kind == "status":
        validate_status(event.get("status"))
        validate_timestamp(event.get("checked_at"))
    elif kind == "waiting":
        value = event.get("next_check_at")
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Invalid check deadline.")
    elif kind in {"error", "lecture_error"}:
        if not isinstance(event.get("error"), str):
            raise ValueError("Invalid worker error.")
        if "notification_error" in event and type(event["notification_error"]) is not bool:
            raise ValueError("Invalid delivery result.")
    elif kind == "lecture":
        validate_lecture(event.get("lecture"))
    elif kind == "meeting":
        if not isinstance(event.get("meeting"), str):
            raise ValueError("Invalid meeting details.")
    return kind in {"status", "waiting", "error", "lecture_error", "lecture", "meeting", "checking", "signin", "notified"}


def stop_process(process):
    """Allow browser cleanup, then bound forced termination as well."""
    try:
        process.stdin.write("stop\n")
        process.stdin.flush()
    except (OSError, ValueError):
        pass
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        else:
            process.kill()
        process.wait(timeout=10)
    process.stdin.close()


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
            validate_dashboard(loaded)
            self.data.update(loaded)
        else:
            for profile in read_profiles():
                self.data["classes"].append({**profile, "id": class_id(profile["url"]), "notification": "default"})
        for item in self.data["classes"]:
            for key, value in {"mode":"public", "interval":60, "continuous":True, "parent":"", "notification":"default"}.items():
                item.setdefault(key, value)
            item["id"] = class_id(item["url"])
            item["state"] = "paused"
            item["error"] = ""
            item.update(check_phase="idle", next_check_at=None)
        self._save()

    def _activity(self, item, kind, message, data=None):
        """Bounded local history containing class data, never raw logs or credentials."""
        data = self.data if data is None else data
        data["activity"].insert(0, {
            "at": datetime.now(timezone.utc).isoformat(), "class_id": item["id"],
            "label": item["label"], "kind": kind, "message": message,
        })
        del data["activity"][200:]

    def _save(self, data=None):
        data = self.data if data is None else data
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        self.data = data

    def _find(self, key):
        return next(item for item in self.data["classes"] if item["id"] == key)

    def snapshot(self):
        with self._lock:
            result = copy.deepcopy(self.data)
            result["notifications"] = notification_configuration()
            result["notification_configured"] = result["notifications"]["configured"]
            for item in result["classes"]:
                item["group_key"] = course_group_key(item)
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
                           continuous=True, notification=notification, state="paused", error="", check_phase="idle", next_check_at=None)
            with self._lock:
                candidate = copy.deepcopy(self.data)
                if old:
                    candidate["classes"] = [profile if p["id"] == key else p for p in candidate["classes"]]
                else:
                    candidate["classes"].append(profile)
                self._activity(profile, "settings", "Class settings updated." if old else "Class added to dashboard.", candidate)
                self._save(candidate)
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
            previous = copy.deepcopy(self.data)
            item.update(state="starting", error="", check_phase="checking", next_check_at=None, run_once=once)
            self._activity(item, "started", "One check requested; will return to paused." if once else "Monitoring started.")
            try:
                threading.Thread(target=self._read_events, args=(key, process), daemon=True).start()
                self._save()
            except Exception:
                try:
                    stop_process(process)
                except (OSError, subprocess.SubprocessError):
                    item.update(state="error", error="Startup failed and the worker could not stop. Try Pause again.")
                else:
                    self._processes.pop(key, None)
                    self.data = previous
                raise

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
                    # CLI informational output is not part of the JSON protocol.
                    continue
                try:
                    if not validate_event(event):
                        continue
                except (ValueError, TypeError):
                    event = {"kind": "error", "error": "Monitor returned an invalid event. Waiting for the next valid check."}
                with self._lock:
                    if self._processes.get(key) is not process:
                        return
                    item = self._find(key)
                    if item["state"] == "stopping":
                        continue
                    if event.get("kind") == "status":
                        previous = item.get("status")
                        changes = describe_changes(status_record(previous), status_record(event["status"])) if previous else []
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
                    elif event.get("kind") == "meeting":
                        item["meeting"] = event["meeting"]
                    elif event.get("kind") == "lecture_error":
                        item["lecture_error"] = event["error"]
                    try:
                        self._save()
                    except OSError:
                        item.update(state="error", error="Could not save dashboard state. Check available disk space and permissions.")
        finally:
            with self._lock:
                if self._processes.get(key) is not process:
                    return
            try:
                code = process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                try:
                    stop_process(process)
                    code = process.returncode
                except (OSError, subprocess.SubprocessError):
                    with self._lock:
                        if self._processes.get(key) is process:
                            self._find(key).update(state="error", error="Monitor could not stop. Try Pause again.")
                    return
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
                    try:
                        self._save()
                    except OSError:
                        item["error"] = "Monitor stopped, but dashboard state could not be saved."

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
                    stop_process(process)
                except (OSError, subprocess.SubprocessError):
                    with self._lock:
                        item.update(state="error", error="Monitor could not stop. Try Pause again.")
                    raise
                with self._lock:
                    self._processes.pop(key, None)
            with self._lock:
                item.update(state="paused", error="", check_phase="idle", next_check_at=None)
                if process:
                    self._activity(item, "paused", "Monitoring paused.")
                self._save()

    def remove(self, key):
        with self._actions:
            self.pause(key)
            with self._lock:
                candidate = copy.deepcopy(self.data)
                candidate["classes"] = [p for p in candidate["classes"] if p["id"] != key]
                # Retain history after removal so past alerts remain explainable.
                self._save(candidate)

    def start_all(self):
        for item in self.snapshot()["classes"]:
            self.start(item["id"])

    def move_class(self, key, direction, *, whole_group=False):
        if direction not in {"earlier", "later"}:
            raise ValueError("Choose a valid card direction.")
        with self._actions, self._lock:
            items = self.data["classes"]
            self._find(key)
            grouped = {}
            for item in items:
                grouped.setdefault(course_group_key(item), []).append(item)
            groups = list(grouped.values())
            group_index = next(i for i, group in enumerate(groups) if any(p["id"] == key for p in group))
            group = groups[group_index]
            # A standalone card moves past an entire neighboring course.
            # Discussion controls only change order inside their own course.
            targets = groups if whole_group or len(group) == 1 else group
            index = group_index if targets is groups else next(i for i, p in enumerate(group) if p["id"] == key)
            destination = index + (-1 if direction == "earlier" else 1)
            if 0 <= destination < len(targets):
                targets[index], targets[destination] = targets[destination], targets[index]
                self._save({**self.data, "classes": [item for group in groups for item in group]})

    def pause_all(self):
        failed = False
        for item in self.snapshot()["classes"]:
            try:
                self.pause(item["id"])
            except (OSError, subprocess.SubprocessError):
                failed = True
        if failed:
            raise OSError("Some monitors could not stop or save their state. Try Pause all again.")

    def set_theme(self, theme):
        if theme not in {"light", "dark", "system"}:
            raise ValueError("Choose light, dark, or system appearance.")
        with self._lock:
            self._save({**self.data, "theme": theme})

    def set_default(self, policy):
        if policy not in POLICIES:
            raise ValueError("Choose a valid notification preference.")
        with self._actions:
            if self.data["default_notification"] == policy:
                return
            with self._lock:
                affected = [p["id"] for p in self.data["classes"] if p.get("notification", "default") == "default" and p["id"] in self._processes]
                self._save({**self.data, "default_notification": policy})
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
            self._save({**self.data, "tray_notice_seen": True})

    def shutdown(self):
        with self._actions:
            self._quitting = True
            self.pause_all()
