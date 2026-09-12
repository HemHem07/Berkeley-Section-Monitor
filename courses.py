"""Shared Berkeley course identity, saved profiles, and worker settings."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

PROFILE_PATH = Path(__file__).with_name(".monitor-profiles.json")


def class_id(url):
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def validate_profile(profile):
    """Validate saved inputs before they reach a worker or either UI."""
    if not isinstance(profile, dict):
        raise ValueError("Invalid saved class.")
    required = ("url", "label", "section_id", "component", "number", "term")
    if any(not isinstance(profile.get(key), str) or not profile[key] for key in required):
        raise ValueError("Saved class is missing its identity. Keep the file for recovery.")
    parsed = urlparse(profile["url"])
    if parsed.scheme != "https" or parsed.netloc != "classes.berkeley.edu" or not parsed.path.startswith("/content/"):
        raise ValueError("Invalid saved Berkeley URL.")
    if not profile["section_id"].isdigit() or not profile["number"].isdigit():
        raise ValueError("Invalid saved class number.")
    if profile["component"] not in {"LEC", "DIS", "LAB", "SEM", "STD", "REC", "TUT", "FLD", "IND", "WEB", "WRK"}:
        raise ValueError("Invalid saved class component.")
    mode, interval = profile.get("mode", "public"), profile.get("interval", 60)
    if mode not in ("public", "calcentral") or type(interval) is not int or interval < 30:
        raise ValueError("Invalid saved monitoring settings.")
    parent = profile.get("parent", "")
    if not isinstance(parent, str) or (mode == "calcentral" and profile["component"] == "DIS" and not parent.isdigit()):
        raise ValueError("Invalid saved parent lecture number.")
    if type(profile.get("continuous", True)) is not bool:
        raise ValueError("Invalid saved monitoring cadence.")


def read_profiles(path: Path = PROFILE_PATH) -> list[dict]:
    if not path.exists():
        return []
    try:
        profiles = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(profiles, list):
            raise ValueError("invalid profile structure")
        for profile in profiles:
            validate_profile(profile)
            for key, value in {"mode":"public", "interval":60, "continuous":True, "parent":""}.items():
                profile.setdefault(key, value)
        return profiles
    except (ValueError, OSError) as exc:
        raise ValueError(f"Cannot read {path.name}: {exc}") from exc


def save_profile(profile: dict, path: Path = PROFILE_PATH) -> None:
    validate_profile(profile)
    profiles = [p for p in read_profiles(path) if p["url"] != profile["url"]]
    profiles.insert(0, profile)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(profiles, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def meeting_details(html: str) -> str:
    from bs4 import BeautifulSoup

    meetings = []
    for block in BeautifulSoup(html, "html.parser").select(".sf--meeting-details"):
        parts = []
        for selector, fallback in ((".sf--meeting-days", ""), (".sf--meeting-time", "Time TBD"), (".sf--location", "Location TBD")):
            node = block.select_one(selector)
            parts.append(" ".join(node.get_text(" ", strip=True).split()) if node else fallback)
        meetings.append(" · ".join(part for part in parts if part))
    return "; ".join(dict.fromkeys(meetings))


def discover_course(url: str) -> dict:
    """Read class identity and counts from an exact Berkeley section page."""
    from monitor import determine_status, fetch_page, locate_section

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
    html = fetch_page(url)
    status = determine_status(locate_section(html))
    if not status.section_id.isdigit():
        raise ValueError("Berkeley did not return a valid class number.")
    return {
        "url": url, "label": f"{course.replace('-', ' ').upper()} {component.upper()} {number}",
        "section_id": status.section_id, "component": component.upper(),
        "number": number, "term": f"{year} {season.title()}", "meeting": meeting_details(html),
    }


def profile_environment(profile: dict) -> dict[str, str]:
    # Every selected-course field overrides stale .env course settings.
    state_key = class_id(profile["url"])
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


