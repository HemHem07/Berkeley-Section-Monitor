"""Resolve parent lectures using Berkeley's published associated-section links."""
import re
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from monitor import MonitorError, fetch_page, locate_section, determine_status

HOST = "https://classes.berkeley.edu"
SECTION = re.compile(r"/content/(\d{4}-(?:fall|spring|summer|winter)-.+)-\d+-(lec|dis|lab|sem|std|rec|tut|fld|ind|web|wrk)-(\d+)/?", re.I)


def resolve_lecture(discussion_url, parent_id=""):
    origin = urlparse(discussion_url)
    match = SECTION.fullmatch(origin.path)
    if origin.scheme != "https" or origin.netloc != "classes.berkeley.edu" or not match or match[2].lower() != "dis":
        raise MonitorError("Lecture context requires a Berkeley discussion section URL.")
    page = BeautifulSoup(fetch_page(discussion_url), "html.parser")
    node = page.select_one('[data-element="associated_sections"][data-section-id]')
    if node is None or not str(node["data-section-id"]).isdigit():
        raise MonitorError("Berkeley did not provide associated-section information.")
    associated = BeautifulSoup(fetch_page(f"{HOST}/sections/associated/{node['data-section-id']}"), "html.parser")
    candidates = {}
    for block in associated.select(".detail-class-associated-sections-flex"):
        link = block.select_one("h4 a[href]")
        if link is None:
            continue
        url = urljoin(HOST, link["href"])
        parsed = urlparse(url)
        target = SECTION.fullmatch(parsed.path)
        if parsed.scheme != "https" or parsed.netloc != "classes.berkeley.edu" or not target:
            continue
        if target[1].lower() != match[1].lower() or target[2].lower() != "lec":
            continue
        identity = re.search(r"Class\s*#:\s*(\d+)\b", block.get_text(" ", strip=True))
        if identity and (not parent_id or identity[1] == parent_id):
            candidates[url] = {"url":url, "section_id":identity[1], "label":link.get_text(" ", strip=True) + " LEC"}
    if len(candidates) != 1:
        raise MonitorError("Could not identify one matching parent lecture from Berkeley's associated sections.")
    return next(iter(candidates.values()))


def read_lecture(lecture):
    status = determine_status(locate_section(fetch_page(lecture["url"]), lecture["section_id"]))
    return {**lecture, "status":asdict(status), "checked_at":datetime.now(timezone.utc).isoformat(), "source":"public"}


class LectureContext:
    def __init__(self, discussion_url, parent_id=""):
        self.url, self.parent_id, self.lecture = discussion_url, parent_id, None

    def refresh(self, emit):
        try:
            if self.lecture is None:
                self.lecture = resolve_lecture(self.url, self.parent_id)
            emit("lecture", lecture=read_lecture(self.lecture))
        except (MonitorError, ValueError, OSError):
            self.lecture = None
            emit("lecture_error", error="Parent lecture unavailable. Will retry after the next discussion check.")
