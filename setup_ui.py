"""Startup picker and local course profiles (no credentials stored here)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import queue
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from monitor import determine_status, fetch_page, locate_section

PROFILE_PATH = Path(__file__).with_name(".monitor-profiles.json")


def read_profiles(path: Path = PROFILE_PATH) -> list[dict]:
    if not path.exists():
        return []
    try:
        profiles = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(profiles, list) or any(
            not isinstance(p, dict) or not isinstance(p.get("url"), str)
            or not isinstance(p.get("label"), str) for p in profiles
        ):
            raise ValueError("invalid profile structure")
        return profiles
    except (ValueError, OSError) as exc:
        raise ValueError(f"Cannot read {path.name}: {exc}") from exc


def save_profile(profile: dict, path: Path = PROFILE_PATH) -> None:
    profiles = [p for p in read_profiles(path) if p["url"] != profile["url"]]
    profiles.insert(0, profile)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(profiles, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def discover_course(url: str) -> dict:
    """Read class identity and counts from an exact Berkeley section page."""
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.netloc != "classes.berkeley.edu":
        raise ValueError("Use an https://classes.berkeley.edu/content/... section link.")
    match = re.fullmatch(
        r"/content/(\d{4})-(fall|spring|summer|winter)-(.+)-(\d+)-(lec|dis|lab|sem|std|rec|tut|fld|ind|web|wrk)-(\d+)/?",
        parsed.path, re.I,
    )
    if not match:
        raise ValueError("Open the individual lecture or discussion page and copy its link.")
    year, season, course, _, component, number = match.groups()
    url = f"https://classes.berkeley.edu{parsed.path.rstrip('/')}"
    status = determine_status(locate_section(fetch_page(url)))
    if not status.section_id.isdigit():
        raise ValueError("Berkeley did not return a valid class number.")
    return {
        "url": url, "label": f"{course.replace('-', ' ').upper()} {component.upper()} {number}",
        "section_id": status.section_id, "component": component.upper(),
        "number": number, "term": f"{year} {season.title()}",
    }


def profile_environment(profile: dict) -> dict[str, str]:
    # Every selected-course field overrides stale .env course settings.
    state_key = hashlib.sha256(profile["url"].encode()).hexdigest()[:16]
    values = {
        "COURSE_URL": profile["url"], "COURSE_LABEL": profile["label"],
        "SECTION_ID": profile["section_id"], "SECTION_COMPONENT": profile["component"],
        "DISCUSSION_NUMBER": profile["number"], "CALCENTRAL_TERM": profile["term"],
        "CALCENTRAL_PARENT_CLASS_NUMBER": profile.get("parent") or profile["section_id"],
        "CHECK_INTERVAL_SECONDS": str(profile["interval"]),
        "STATE_FILE": str(PROFILE_PATH.parent / ".monitor-state" / f"{state_key}-{profile['mode']}.json"),
    }
    return values


def apply_profile(profile: dict) -> None:
    os.environ.update(profile_environment(profile))


def choose_course(*, calcentral: bool = False, interval: int = 60) -> dict | list[dict] | None:
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
        root = tk.Tk()
    except (ImportError, RuntimeError) as exc:
        raise ValueError("The picker needs Python with Tk support. Use --no-ui for terminal-only operation.") from exc
    except Exception as exc:
        raise ValueError(f"Cannot open the picker: {exc}. Use --no-ui on a headless machine.") from exc
    root.title("Berkeley Section Monitor")
    root.geometry("760x680")
    root.minsize(710, 650)
    root.columnconfigure(0, weight=1)
    frame = ttk.Frame(root, padding=24)
    frame.grid(sticky="nsew")
    frame.columnconfigure(1, weight=1)
    ttk.Label(frame, text="Choose a class to monitor", font=("Segoe UI", 18, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 16))
    try:
        profiles = read_profiles()
    except ValueError:
        root.destroy()
        raise
    saved = tk.StringVar(value="Add a class…")
    choices = ["Add a class…"] + [f"{p['label']} · {p.get('term', '')}" for p in profiles]
    menu = ttk.Combobox(frame, textvariable=saved, values=choices, state="readonly")
    ttk.Label(frame, text="Saved classes").grid(row=1, column=0, sticky="w", padx=(0, 16))
    menu.grid(row=1, column=1, sticky="ew", pady=5)
    url = tk.StringVar(value=os.getenv("COURSE_URL", ""))
    parent = tk.StringVar(value=os.getenv("CALCENTRAL_PARENT_CLASS_NUMBER", ""))
    mode = tk.StringVar(value="calcentral" if calcentral else "public")
    seconds = tk.StringVar(value=str(interval))
    continuous = tk.BooleanVar(value=True)
    for row, label, variable in [(2, "Berkeley section URL", url), (4, "Parent lecture class #", parent)]:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16))
        ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
    ttk.Button(frame, text="Find a class on Berkeley Class Search ↗",
               command=lambda: webbrowser.open("https://classes.berkeley.edu/search/class")).grid(
                   row=3, column=1, sticky="w", pady=(2, 10))
    ttk.Label(frame, text="Parent # is needed only for CalCentral discussions.\nLecture-only classes use their own section link.").grid(
        row=5, column=1, sticky="w", pady=(0, 12))
    ttk.Label(frame, text="Data source").grid(row=6, column=0, sticky="w")
    ttk.Combobox(frame, textvariable=mode, values=["public", "calcentral"], state="readonly").grid(
        row=6, column=1, sticky="ew", pady=5)
    ttk.Label(frame, text="Seconds between checks").grid(row=7, column=0, sticky="w", padx=(0, 16))
    ttk.Spinbox(frame, textvariable=seconds, from_=30, to=86400, increment=30).grid(
        row=7, column=1, sticky="ew", pady=5)
    ttk.Checkbutton(frame, text="Keep checking until Ctrl+C in the terminal", variable=continuous).grid(
        row=8, column=1, sticky="w", pady=5)
    status = tk.StringVar(value="Class number and capacities are read automatically from Berkeley.")
    ttk.Label(frame, textvariable=status, wraplength=640).grid(row=9, column=0, columnspan=2, sticky="w", pady=10)
    result = None
    pending = queue.Queue()
    queued = []
    adding = False
    ttk.Label(frame, text="Monitoring list (add each class, then start)").grid(
        row=11, column=0, columnspan=2, sticky="w", pady=(12, 4))
    course_list = tk.Listbox(frame, height=5, exportselection=False)
    course_list.grid(row=12, column=0, columnspan=2, sticky="ew")

    def refresh_queue():
        course_list.delete(0, tk.END)
        for item in queued:
            schedule = f"every {item['interval']}s" if item["continuous"] else "once"
            course_list.insert(tk.END, f"{item['label']} · {item['term']} · {item['mode']} · {schedule}")

    def remove_selected():
        for index in reversed(course_list.curselection()):
            del queued[index]
        refresh_queue()

    ttk.Button(frame, text="Remove selected", command=remove_selected).grid(
        row=13, column=0, sticky="w", pady=5)

    def select(_event=None):
        index = menu.current() - 1
        if index < 0:
            url.set("")
            parent.set("")
            return
        profile = profiles[index]
        url.set(profile["url"])
        parent.set(profile.get("parent", ""))
        mode.set(profile.get("mode", "public"))
        seconds.set(str(profile.get("interval", 60)))
        continuous.set(profile.get("continuous", True))

    menu.bind("<<ComboboxSelected>>", select)
    if profiles:
        menu.current(1)
        select()

    def finish():
        nonlocal result
        try:
            value = pending.get_nowait()
        except queue.Empty:
            root.after(100, finish)
            return
        if isinstance(value, Exception):
            start.configure(state="normal")
            add.configure(state="normal")
            status.set("Could not load that class. Check the link and try again.")
            messagebox.showerror("Class setup", str(value), parent=root)
            return
        try:
            save_profile(value)
        except (ValueError, OSError) as exc:
            start.configure(state="normal")
            add.configure(state="normal")
            messagebox.showerror("Save failed", str(exc), parent=root)
            return
        if adding:
            queued[:] = [p for p in queued if p["url"] != value["url"]]
            queued.append(value)
            refresh_queue()
            profiles[:] = [value] + [p for p in profiles if p["url"] != value["url"]]
            menu.configure(values=["Add a class…"] + [f"{p['label']} · {p.get('term', '')}" for p in profiles])
            menu.current(0)
            select()
            start.configure(state="normal")
            add.configure(state="normal")
            status.set(f"{len(queued)} class(es) ready. Add another or click Start monitoring to run the list.")
            return
        result = value
        root.destroy()

    def launch(add_to_list=False):
        nonlocal adding, result
        if queued and not add_to_list:
            result = list(queued)
            root.destroy()
            return
        adding = add_to_list
        try:
            interval_value = int(seconds.get())
            if interval_value < 30:
                raise ValueError("Choose at least 30 seconds between checks.")
            selected = {"mode": mode.get(), "parent": parent.get().strip(),
                        "interval": interval_value, "continuous": continuous.get()}
            selected_url = url.get().strip()
        except ValueError as exc:
            messagebox.showerror("Class setup", str(exc), parent=root)
            return
        start.configure(state="disabled")
        add.configure(state="disabled")
        status.set("Reading the class details from Berkeley…")

        def load():
            try:
                profile = discover_course(selected_url)
                profile.update(selected)
                if profile["mode"] == "calcentral" and profile["component"] == "DIS" and not profile["parent"].isdigit():
                    raise ValueError("Enter the parent lecture's class number for a CalCentral discussion.")
                pending.put(profile)
            except Exception as exc:
                pending.put(exc)

        threading.Thread(target=load, daemon=True).start()
        root.after(100, finish)

    start = ttk.Button(frame, text="Start monitoring", command=launch)
    start.grid(row=10, column=1, sticky="e", pady=8)
    add = ttk.Button(frame, text="Add to monitoring list", command=lambda: launch(True))
    add.grid(row=10, column=0, sticky="w", pady=8)
    root.mainloop()
    return result
