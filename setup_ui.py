"""Optional Tk startup picker; shared course handling lives in courses."""
from __future__ import annotations

import os
import threading
import queue
import webbrowser

from courses import discover_course, read_profiles, save_profile


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
