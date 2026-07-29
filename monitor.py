"""One-shot UC Berkeley class section availability monitor.

Run this script from cron or another scheduler. It fetches once, logs once,
persists the observed state, and only notifies on a closed-to-open transition.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import smtplib
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

DEFAULT_URL = (
    "https://classes.berkeley.edu/content/"
    "2026-fall-math-113-104-dis-104"
)
DEFAULT_CALCENTRAL_URL = "https://calcentral.berkeley.edu/academics"
DEFAULT_ENROLLMENT_CENTER_URL = (
    "https://bcsweb.is.berkeley.edu/psc/bcsprd/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_MD_SP_FL.GBL"
    "?Action=U&MD=Y&GMenu=SSR_STUDENT_FL&GComp=SSR_START_PAGE_FL"
    "&GPage=SSR_START_PAGE_FL&scname=CS_SSR_MANAGE_CLASSES_NAV"
    "&AJAXTransfer=y&ICAJAXTrf=true&ICMDListSlideout=true"
)
OPEN_STATUS_CODES = {"O", "OPEN"}
TIMEOUT_SECONDS = 30

logger = logging.getLogger("section_monitor")


class MonitorError(RuntimeError):
    """Expected failure that should be logged without a traceback."""


@dataclass(frozen=True)
class SectionStatus:
    section_id: str
    status_code: str
    status_description: str
    enrolled: int
    capacity: int
    waitlisted: int
    waitlist_capacity: int
    open_reserved: int
    is_open: bool


def fetch_page(url: str, session: requests.Session | None = None) -> str:
    """Fetch the public class page and return its HTML.

    Berkeley currently rejects Python requests based on its TLS fingerprint on
    some edges. A curl fallback remains a direct HTTP request (not a browser)
    and makes scheduled runs reliable across those edges.
    """
    client = session or requests.Session()
    try:
        response = client.get(
            url,
            timeout=TIMEOUT_SECONDS,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        if response.status_code == 403 and session is None:
            return _fetch_page_with_curl(url)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise MonitorError(f"could not fetch class page: {exc}") from exc
    return response.text


def _fetch_page_with_curl(url: str) -> str:
    try:
        result = subprocess.run(
            [
                "curl",
                "--fail",
                "--location",
                "--compressed",
                "--silent",
                "--show-error",
                "--max-time",
                str(TIMEOUT_SECONDS),
                "--user-agent",
                "Mozilla/5.0",
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS + 5,
        )
    except FileNotFoundError as exc:
        raise MonitorError(
            "Berkeley rejected the Python request and curl is not installed"
        ) from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise MonitorError(f"could not fetch class page with curl: {detail.strip()}") from exc
    return result.stdout


def locate_section(html: str, section_id: str | None = None) -> dict[str, Any]:
    """Extract the requested enrollment record from embedded Drupal JSON."""
    soup = BeautifulSoup(html, "html.parser")
    settings_tag = soup.find(
        "script",
        attrs={
            "type": "application/json",
            "data-drupal-selector": "drupal-settings-json",
        },
    )
    if settings_tag is None or not settings_tag.string:
        raise MonitorError("Drupal enrollment JSON was not found; the page may have changed")

    try:
        settings = json.loads(settings_tag.string)
        enrollment = settings["ucb"]["enrollment"]["available"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise MonitorError(
            "embedded JSON did not contain the expected enrollment data"
        ) from exc

    actual_id = str(enrollment.get("id", ""))
    if section_id and actual_id != str(section_id):
        raise MonitorError(
            f"page contains enrollment ID {actual_id!r}, not requested ID {section_id!r}"
        )
    return enrollment


def determine_status(section: dict[str, Any]) -> SectionStatus:
    """Convert an enrollment record into a normalized availability result."""
    try:
        data = section["enrollmentStatus"]
        status = data["status"]
        code = str(status["code"]).upper()
        description = str(status["description"])
        enrolled = int(data["enrolledCount"])
        capacity = int(data["maxEnroll"])
        waitlisted = int(data.get("waitlistedCount", 0))
        waitlist_capacity = int(data.get("maxWaitlist", 0))
        open_reserved = int(data.get("openReserved", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise MonitorError("enrollment record has an unexpected structure") from exc

    # Berkeley's explicit status is authoritative. Seat counts are a fallback
    # for a future payload that uses a new/blank status code.
    if code in OPEN_STATUS_CODES:
        is_open = True
    elif code in {"C", "CLOSED", "W", "WAITLISTED"}:
        is_open = False
    else:
        is_open = capacity > enrolled or open_reserved > 0

    return SectionStatus(
        section_id=str(section.get("id", "")),
        status_code=code,
        status_description=description,
        enrolled=enrolled,
        capacity=capacity,
        waitlisted=waitlisted,
        waitlist_capacity=waitlist_capacity,
        open_reserved=open_reserved,
        is_open=is_open,
    )


def parse_calcentral_text(text: str, section_id: str) -> SectionStatus:
    """Parse aggregate availability from a PeopleSoft class-detail page."""
    import re

    normalized = " ".join(text.split())

    def number(*labels: str, required: bool = True, default: int = 0) -> int:
        for label in labels:
            match = re.search(
                rf"\b{re.escape(label)}\b\s*:?\s*(\d+)",
                normalized,
                flags=re.IGNORECASE,
            )
            if match:
                return int(match.group(1))
        if required:
            raise MonitorError(
                "CalCentral page is not showing the expected class enrollment "
                f"details (missing {labels[0]!r}). Navigate to the specific "
                "discussion's Enrollment Information page and try again."
            )
        return default

    enrolled = number("Enrollment Total", "Enrolled")
    capacity = number("Enrollment Capacity", "Capacity")
    waitlisted = number("Wait List Total", "Waitlisted", required=False)
    waitlist_capacity = number(
        "Wait List Capacity", "Waitlist Max", required=False
    )
    available_match = re.search(
        r"\b(?:Available Seats|Open Seats|Total Open Seats)\b\s*:?\s*(\d+)",
        normalized,
        flags=re.IGNORECASE,
    )
    available = int(available_match.group(1)) if available_match else capacity - enrolled

    status_match = re.search(
        r"\bStatus\b\s*:?\s*(Open|Closed|Waitlist(?:ed)?)\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if status_match:
        description = status_match.group(1).title()
        code = "O" if description.lower() == "open" else "C"
        is_open = code == "O"
    else:
        is_open = available > 0
        code = "O" if is_open else "C"
        description = "Open" if is_open else "Closed"

    return SectionStatus(
        section_id=section_id,
        status_code=code,
        status_description=description,
        enrolled=enrolled,
        capacity=capacity,
        waitlisted=waitlisted,
        waitlist_capacity=waitlist_capacity,
        open_reserved=0,
        is_open=is_open,
    )


def parse_calcentral_row(
    cells: list[str],
    section_id: str,
    discussion_number: str | None = None,
) -> SectionStatus:
    """Parse one PeopleSoft Discussion Section table row."""
    import re

    cleaned = [" ".join(cell.split()) for cell in cells]
    if len(cleaned) < 5:
        raise MonitorError(
            "CalCentral's discussion row has fewer columns than expected"
        )

    section_cell = cleaned[1]
    if f"#{section_id}" not in section_cell:
        raise MonitorError(
            f"CalCentral row is not for requested class number {section_id}"
        )
    if discussion_number and not section_cell.startswith(f"{discussion_number} "):
        raise MonitorError(
            f"CalCentral row is not Discussion {discussion_number}"
        )

    status_match = re.search(r"\b(Open|Closed|Waitlist(?:ed)?)\b", section_cell, re.I)
    if not status_match:
        raise MonitorError("CalCentral discussion row has no recognizable status")
    description = status_match.group(1).title()

    try:
        open_seats = int(cleaned[2])
        capacity = int(cleaned[3])
        waitlist_match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", cleaned[4])
        if not waitlist_match:
            raise ValueError("unexpected waitlist value")
        waitlisted = int(waitlist_match.group(1))
        waitlist_capacity = int(waitlist_match.group(2))
    except ValueError as exc:
        raise MonitorError(
            "CalCentral discussion counts have an unexpected format"
        ) from exc

    is_open = description.lower() == "open" and open_seats > 0
    return SectionStatus(
        section_id=section_id,
        status_code="O" if is_open else "C",
        status_description=description,
        enrolled=max(capacity - open_seats, 0),
        capacity=capacity,
        waitlisted=waitlisted,
        waitlist_capacity=waitlist_capacity,
        open_reserved=0,
        is_open=is_open,
    )


def load_previous_status(path: Path) -> SectionStatus | None:
    """Load the last successful status, returning None on first run."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return SectionStatus(**raw["status"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise MonitorError(f"could not read state file {path}: {exc}") from exc


def save_previous_status(path: Path, status: SectionStatus) -> None:
    """Atomically persist the latest successful status."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": asdict(status),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise MonitorError(f"could not save state file {path}: {exc}") from exc


def describe_changes(previous: SectionStatus, current: SectionStatus) -> list[str]:
    """Return human-readable enrollment fields that changed."""
    changes = []
    fields = (
        ("Status", "status_description"),
        ("Enrolled", "enrolled"),
        ("Capacity", "capacity"),
        ("Waitlisted", "waitlisted"),
        ("Waitlist capacity", "waitlist_capacity"),
        ("Open reserved seats", "open_reserved"),
    )
    for label, field in fields:
        before = getattr(previous, field)
        after = getattr(current, field)
        if before != after:
            changes.append(f"{label}: {before} → {after}")
    return changes


def send_notification(
    status: SectionStatus,
    course_url: str,
    previous: SectionStatus | None = None,
    *,
    ping: bool = False,
) -> None:
    """Send through a webhook or SMTP, selected entirely by environment."""
    discussion_number = os.getenv("DISCUSSION_NUMBER", "104").strip()
    course_label = os.getenv(
        "COURSE_LABEL", f"MATH 113 discussion {discussion_number}"
    ).strip()
    checked_at = datetime.now(ZoneInfo("America/Los_Angeles")).strftime(
        "%Y-%m-%d %I:%M:%S %p %Z"
    )
    if status.status_code == "TEST":
        subject = "TEST: MATH 113 monitor notifications are working"
        body = (
            f"{subject}\n\n"
            "This is only a delivery test. It does not indicate that the "
            "discussion section is open.\n\n"
            f"Checked: {checked_at} (Pacific Time)\n"
            f"Monitored page: {course_url}"
        )
    else:
        changes = describe_changes(previous, status) if previous else []
        subject = (
            f"{course_label} is open"
            if status.is_open and previous and not previous.is_open
            else (
                f"{course_label} enrollment changed"
                if changes
                else f"{course_label} checked — no change"
            )
        )
        body = (
            f"{subject}!\n\n"
            + ("\n".join(changes) + "\n\n" if changes else "")
            +
            f"Status: {status.status_description} ({status.status_code})\n"
            f"Enrollment: {status.enrolled}/{status.capacity}\n"
            f"Waitlist: {status.waitlisted}/{status.waitlist_capacity}\n"
            f"Checked: {checked_at} (Pacific Time)\n"
            f"{course_url}"
        )

    webhook_url = os.getenv("NOTIFICATION_WEBHOOK_URL")
    if webhook_url:
        discord_user_id = os.getenv("DISCORD_USER_ID", "").strip()
        if ping and discord_user_id:
            body = f"<@{discord_user_id}> {body}"
        try:
            response = requests.post(
                webhook_url,
                json={"text": body, "content": body},
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise MonitorError(f"webhook notification failed: {exc}") from exc
        return

    smtp_host = os.getenv("SMTP_HOST")
    if smtp_host:
        required = ("SMTP_FROM", "NOTIFY_EMAIL")
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise MonitorError(f"missing email environment variables: {', '.join(missing)}")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = os.environ["SMTP_FROM"]
        message["To"] = os.environ["NOTIFY_EMAIL"]
        message.set_content(body)
        try:
            port = int(os.getenv("SMTP_PORT", "587"))
            with smtplib.SMTP(smtp_host, port, timeout=TIMEOUT_SECONDS) as server:
                server.starttls()
                username = os.getenv("SMTP_USERNAME")
                password = os.getenv("SMTP_PASSWORD")
                if username and password:
                    server.login(username, password)
                server.send_message(message)
        except (OSError, smtplib.SMTPException, ValueError) as exc:
            raise MonitorError(f"email notification failed: {exc}") from exc
        return

    raise MonitorError(
        "section opened, but no NOTIFICATION_WEBHOOK_URL or SMTP_HOST is configured"
    )


def run_check(
    url: str,
    section_id: str | None,
    state_path: Path,
    session: requests.Session | None = None,
) -> SectionStatus:
    """Run one check; notify only after a known non-open state becomes open."""
    html = fetch_page(url, session)
    section = locate_section(html, section_id)
    current = determine_status(section)
    previous = load_previous_status(state_path)

    logger.info(
        "section_id=%s status=%s description=%r enrolled=%d/%d waitlist=%d/%d open=%s",
        current.section_id,
        current.status_code,
        current.status_description,
        current.enrolled,
        current.capacity,
        current.waitlisted,
        current.waitlist_capacity,
        current.is_open,
    )

    changed = previous is not None and bool(describe_changes(previous, current))
    webhook_configured = bool(os.getenv("NOTIFICATION_WEBHOOK_URL"))
    if changed or webhook_configured:
        send_notification(current, url, previous, ping=changed)
        logger.info(
            "notification sent (%s)",
            "enrollment changed; Discord ping included" if changed else "no change",
        )

    # Save only after notification succeeds, so a transient notification failure
    # will be retried by the next scheduled run.
    save_previous_status(state_path, current)
    return current


def process_status(
    current: SectionStatus,
    course_url: str,
    state_path: Path,
) -> SectionStatus:
    """Log, notify, and persist a status obtained from any data source."""
    previous = load_previous_status(state_path)
    logger.info(
        "section_id=%s status=%s description=%r enrolled=%d/%d waitlist=%d/%d open=%s",
        current.section_id,
        current.status_code,
        current.status_description,
        current.enrolled,
        current.capacity,
        current.waitlisted,
        current.waitlist_capacity,
        current.is_open,
    )
    changed = previous is not None and bool(describe_changes(previous, current))
    if changed or os.getenv("NOTIFICATION_WEBHOOK_URL"):
        send_notification(current, course_url, previous, ping=changed)
        logger.info(
            "notification sent (%s)",
            "enrollment changed; Discord ping included" if changed else "no change",
        )
    save_previous_status(state_path, current)
    return current


def run_calcentral(
    section_id: str,
    discussion_number: str,
    state_path: Path,
    interval_seconds: int,
    continuous: bool,
) -> int:
    """Search PeopleSoft and monitor one discussion row in Chrome."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise MonitorError(
            "CalCentral mode needs Playwright. Run: "
            ".venv/bin/pip install -r requirements.txt"
        ) from exc

    profile = Path(
        os.getenv("CALCENTRAL_PROFILE_DIR", ".calcentral-browser-profile")
    ).resolve()
    start_url = os.getenv("CALCENTRAL_START_URL", DEFAULT_CALCENTRAL_URL)
    enrollment_url = os.getenv(
        "CALCENTRAL_ENROLLMENT_URL", DEFAULT_ENROLLMENT_CENTER_URL
    )
    parent_class_number = os.getenv(
        "CALCENTRAL_PARENT_CLASS_NUMBER", "22491"
    ).strip()
    term_name = os.getenv("CALCENTRAL_TERM", "2026 Fall").strip()
    logger.info("opening the private CalCentral browser profile at %s", profile)

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                channel="chrome",
                headless=False,
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
            print(
                "\nIn the opened Chrome window:\n"
                "  1. Sign in with CalNet/Duo if asked.\n"
                "  2. Wait until the CalCentral academics page loads.\n"
                "Then return here and press Enter. The program will search for\n"
                f"MATH 113 and Discussion {discussion_number} automatically; "
                "it will not select\n"
                "a section, add it to your cart, or enroll.\n"
            )
            input("Press Enter when CalCentral is signed in... ")

            while True:
                try:
                    status = fetch_calcentral_status(
                        page,
                        enrollment_url=enrollment_url,
                        term_name=term_name,
                        parent_class_number=parent_class_number,
                        section_id=section_id,
                        discussion_number=discussion_number,
                    )
                    process_status(status, page.url, state_path)
                except MonitorError as exc:
                    logger.error("%s", exc)
                except PlaywrightError as exc:
                    logger.error("CalCentral browser check failed: %s", exc)

                if not continuous:
                    context.close()
                    return 0
                time.sleep(interval_seconds)
    except PlaywrightError as exc:
        raise MonitorError(f"could not start the CalCentral browser: {exc}") from exc


def fetch_calcentral_status(
    page: Any,
    *,
    enrollment_url: str,
    term_name: str,
    parent_class_number: str,
    section_id: str,
    discussion_number: str,
) -> SectionStatus:
    """Navigate the read-only PeopleSoft class search and extract one row."""
    logger.info("CalCentral: opening Enrollment Center")
    page.goto(enrollment_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(500)
    logger.info("CalCentral: Enrollment Center loaded")

    if (
        "auth.berkeley.edu" in page.url
        or page.get_by_role("link", name="Sign in", exact=True).count() == 1
    ):
        raise MonitorError(
            "CalCentral session expired; sign in again in the opened browser"
        )

    search = page.locator("#CW_CLSRCH_WRK2_PTUN_KEYWORD")
    deadline = time.monotonic() + 30
    class_search_clicked = False
    term_clicked = False

    while time.monotonic() < deadline and search.count() != 1:
        menu = page.get_by_role("button", name="Enrollment Center", exact=True)
        if (
            menu.count() == 1
            and menu.get_attribute("aria-expanded") != "true"
        ):
            menu.click()
            page.wait_for_timeout(250)

        if not class_search_clicked:
            class_search = page.get_by_role(
                "button", name="Class Search and Enroll", exact=True
            )
            if class_search.count() == 1:
                already_selected = (
                    class_search.get_attribute("aria-selected") == "true"
                    or class_search.get_attribute("aria-current") == "page"
                )
                if already_selected:
                    logger.info(
                        "CalCentral: Class Search and Enroll is already selected"
                    )
                else:
                    logger.info("CalCentral: opening Class Search and Enroll")
                    class_search.click()
                class_search_clicked = True
                page.wait_for_timeout(250)
                continue

        if not term_clicked:
            term_link = page.get_by_role("link", name=term_name, exact=True)
            if term_link.count() == 1:
                logger.info("CalCentral: selecting term %s", term_name)
                term_link.click()
                term_clicked = True
                page.wait_for_timeout(250)
                continue

        page.wait_for_timeout(250)

    if search.count() != 1:
        raise MonitorError(
            "CalCentral class-number search field did not appear within 30 "
            f"seconds (page={page.url!r}). The PeopleSoft session may have "
            "expired; sign in again and restart the monitor."
        )
    logger.info(
        "CalCentral: searching parent class number %s", parent_class_number
    )
    # PeopleSoft does not always update its component state after Playwright's
    # fast fill(). Type real key events so its own key handlers see the value,
    # then submit from the field.
    search.click()
    search.press("ControlOrMeta+A")
    search.type(parent_class_number, delay=50)
    page.wait_for_timeout(250)
    search.press("Enter")

    row = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and row is None:
        for frame in page.frames:
            discussion_heading = frame.get_by_text(
                "Discussion Section", exact=True
            )
            if discussion_heading.count() == 1:
                discussion_heading.scroll_into_view_if_needed()

            candidate = frame.get_by_role("row").filter(
                has_text=f"{discussion_number} #{section_id}"
            )
            if candidate.count() == 1:
                row = candidate
                break
        if row is None:
            page.wait_for_timeout(250)

    if row is None:
        raise MonitorError(
            f"Discussion class number {section_id} was not found in the "
            f"{parent_class_number} component table"
        )
    logger.info(
        "CalCentral: found Discussion %s, class number %s",
        discussion_number,
        section_id,
    )
    cells = row.get_by_role("cell").all_inner_texts()
    if not cells:
        cells = row.locator("td").all_inner_texts()
    return parse_calcentral_row(
        cells,
        section_id,
        discussion_number,
    )


def run_continuously(
    url: str,
    section_id: str | None,
    state_path: Path,
    interval_seconds: int,
) -> int:
    """Check repeatedly until interrupted, surviving individual check errors."""
    logger.info(
        "continuous monitoring started; interval=%d seconds (press Ctrl+C to stop)",
        interval_seconds,
    )
    try:
        while True:
            try:
                run_check(url, section_id, state_path)
            except MonitorError as exc:
                logger.error("%s", exc)
            except Exception:
                logger.exception("unexpected monitoring error")
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        logger.info("monitoring stopped")
        return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor a UC Berkeley section's enrollment status."
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="check repeatedly until Ctrl+C (default: check once and exit)",
    )
    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="send a test notification immediately and exit",
    )
    parser.add_argument(
        "--calcentral",
        action="store_true",
        help="read the live section detail from an interactive CalCentral browser",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("CHECK_INTERVAL_SECONDS", "300")),
        metavar="SECONDS",
        help="continuous-mode interval (default: 300 or CHECK_INTERVAL_SECONDS)",
    )
    args = parser.parse_args(argv)
    if args.interval < 30:
        parser.error("--interval must be at least 30 seconds")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    url = os.getenv("COURSE_URL", DEFAULT_URL)
    section_id = os.getenv("SECTION_ID") or None
    discussion_number = os.getenv("DISCUSSION_NUMBER", "104").strip()
    default_state_file = (
        f".calcentral_section_status_{section_id or '27743'}.json"
        if args.calcentral
        else ".section_status.json"
    )
    state_path = Path(os.getenv("STATE_FILE", default_state_file))
    if args.test_notification:
        test_status = SectionStatus(
            section_id=section_id or "27743",
            status_code="TEST",
            status_description="Test notification",
            enrolled=39,
            capacity=40,
            waitlisted=0,
            waitlist_capacity=6,
            open_reserved=0,
            is_open=True,
        )
        try:
            send_notification(test_status, url)
        except MonitorError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("test notification sent successfully")
        return 0
    if args.calcentral:
        try:
            return run_calcentral(
                section_id or "27743",
                discussion_number,
                state_path,
                args.interval,
                args.continuous,
            )
        except MonitorError as exc:
            logger.error("%s", exc)
            return 1
    if args.continuous:
        return run_continuously(url, section_id, state_path, args.interval)
    try:
        run_check(url, section_id, state_path)
    except MonitorError as exc:
        logger.error("%s", exc)
        return 1
    except Exception:
        logger.exception("unexpected monitoring error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
