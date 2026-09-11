"""UC Berkeley enrollment monitor with a startup picker and continuous checks."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import smtplib
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

import requests
from bs4 import BeautifulSoup

DEFAULT_URL = (
    "https://classes.berkeley.edu/content/"
    "2026-fall-math-113-104-dis-104"
)
DEFAULT_CALCENTRAL_URL = "https://calcentral.berkeley.edu/academics"
DEFAULT_PUBLIC_SECTION_URL_TEMPLATE = (
    "https://classes.berkeley.edu/content/"
    "2026-fall-math-113-{discussion_number}-dis-{discussion_number}"
)
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


class SignInRequired(MonitorError):
    """The browser explicitly shows authentication or an expired session."""


def require_signed_in(page: Any) -> None:
    from urllib.parse import urlparse

    for frame in [page, *page.frames]:
        host = urlparse(frame.url).hostname or ""
        if host == "auth.berkeley.edu" or host.endswith(".duosecurity.com"):
            raise SignInRequired("CalCentral needs CalNet/Duo sign-in.")
        sign_in = frame.get_by_role("link", name="Sign in", exact=True)
        if sign_in.count() and sign_in.first.is_visible():
            raise SignInRequired("CalCentral needs a fresh sign-in.")
        expired = frame.get_by_text(re.compile(r"(?:your )?session (?:has )?expired", re.I))
        if expired.count() and expired.first.is_visible():
            raise SignInRequired("CalCentral session expired.")


def send_signin_notification() -> None:
    """Send a dedicated actionable alert without touching enrollment state."""
    webhook = os.getenv("NOTIFICATION_WEBHOOK_URL")
    if not webhook:
        logger.warning("Sign-in needed; no NOTIFICATION_WEBHOOK_URL is configured.")
        return
    user_id = os.getenv("DISCORD_USER_ID", "").strip()
    mention = f"<@{user_id}> " if user_id.isdigit() else ""
    body = (
        f"{mention}CalCentral sign-in required — enrollment monitoring is paused.\n"
        "Return to the computer running Berkeley Section Monitor, sign in with "
        "CalNet/Duo in its Chrome window, including any Enrollment Center verification. "
        "Background checks will resume automatically."
    )
    try:
        response = requests.post(webhook, json={
            "content": body, "text": body,
            "allowed_mentions": {"parse": [], "users": [user_id] if user_id.isdigit() else []},
        }, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise MonitorError("Could not deliver the CalCentral sign-in alert.") from exc


def send_startup_notification(source: str, interval: int, continuous: bool) -> bool:
    """Announce the first successful check without allowing any Discord mentions."""
    webhook = os.getenv("NOTIFICATION_WEBHOOK_URL")
    if not webhook:
        return True
    label = os.getenv("COURSE_LABEL", "Berkeley section monitor")
    cadence = f"Checking every {interval} seconds after each check." if continuous else "One-time check completed."
    body = f"Monitor started: {label}\nSource: {source}\nFirst check succeeded. {cadence}"
    try:
        response = requests.post(webhook, json={
            "content": body, "text": body,
            "allowed_mentions": {"parse": [], "users": [], "roles": [], "replied_user": False},
        }, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException:
        logger.warning("Startup message could not be delivered; will retry after the next successful check.")
        return False
    return True


def wait_for_enrollment_entry(page: Any, timeout: float = 30, auth_grace: float = 15) -> None:
    """Let CalNet's automatic SSO redirect finish before declaring sign-in needed."""
    from urllib.parse import urlparse

    deadline = time.monotonic() + timeout
    auth_since = None
    while time.monotonic() < deadline:
        try:
            require_signed_in(page)
        except SignInRequired:
            if auth_since is None:
                auth_since = time.monotonic()
            if time.monotonic() - auth_since >= auth_grace:
                raise
        else:
            auth_since = None
            if urlparse(page.url).hostname == "bcsweb.is.berkeley.edu":
                heading = page.get_by_role("heading", name="Enrollment Center", exact=True)
                if heading.count() and heading.first.is_visible():
                    return
        page.wait_for_timeout(250)
    require_signed_in(page)
    raise MonitorError("Enrollment Center did not finish loading within 30 seconds.")


def close_browser_context(context: Any) -> None:
    """Closing the window manually must not mask the original error or Ctrl+C."""
    if context is None:
        return
    try:
        context.close()
    except Exception as exc:
        # Driver disconnection can arrive as a plain Exception rather than
        # playwright.sync_api.Error. Only ignore known shutdown conditions.
        message = str(exc).lower()
        if not any(reason in message for reason in (
            "connection closed while reading from the driver",
            "target page, context or browser has been closed",
            "event loop is closed",
        )):
            raise


def complete_calcentral_login(page: Any, timeout: float = 600) -> None:
    """Wait for both CalCentral and PeopleSoft login, including a second Duo prompt."""
    from urllib.parse import urlparse

    print("\nComplete CalNet/Duo in the monitor's Chrome window. No terminal input is needed.")
    print("Enrollment Center may request a second Duo verification after Academics loads.")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        host = urlparse(page.url).hostname
        if host == "calcentral.berkeley.edu":
            enrollment = page.get_by_role("link", name="Enrollment Center", exact=True)
            if enrollment.count() and enrollment.first.is_visible():
                logger.info("Academics signed in; opening Enrollment Center to complete its sign-in.")
                enrollment.click()
        elif host == "bcsweb.is.berkeley.edu":
            heading = page.get_by_role("heading", name="Enrollment Center", exact=True)
            if heading.count() and heading.first.is_visible():
                require_signed_in(page)
                logger.info("Enrollment Center sign-in confirmed. Resuming monitoring.")
                return
        page.wait_for_timeout(500)
    raise MonitorError("Sign-in was not completed within 10 minutes. Restart the monitor to try again.")


@dataclass(frozen=True)
class SectionStatus:
    section_id: str
    status_code: str
    status_description: str
    enrolled: int
    capacity: int
    waitlisted: int
    waitlist_capacity: int | None
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
        waitlist_capacity = int(data["maxWaitlist"]) if data.get("maxWaitlist") is not None else None
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

    def number(*labels: str, required: bool = True, default: int | None = 0) -> int | None:
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
        "Wait List Capacity", "Waitlist Capacity", "Waitlist Max", required=False, default=None
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
    waitlist_capacity: int | None = None,
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
        # PeopleSoft's component table displays waitlisted count / section
        # enrollment capacity (for example 0 / 40), not the configured
        # waitlist maximum. Use the fetched metadata or leave it unknown.
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


def describe_availability(status: SectionStatus) -> str:
    """Return a clear user-facing explanation of the enrollment state."""
    description = status.status_description.lower()
    if status.is_open:
        return "Open — seats available for immediate enrollment"
    if "waitlist" in description:
        return "Waitlist — section full; waitlist available"
    if description == "closed":
        return "Closed — section and waitlist unavailable"
    return status.status_description


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
        subject = f"TEST: {course_label} monitor notifications are working"
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
            f"Status: {describe_availability(status)}\n"
            f"Enrollment: {status.enrolled}/{status.capacity}\n"
            f"Waitlist: {status.waitlisted}/{status.waitlist_capacity if status.waitlist_capacity is not None else 'unknown'}\n"
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
        "section_id=%s status=%s description=%r enrolled=%d/%d waitlist=%d/%s open=%s",
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
        "section_id=%s status=%s description=%r enrolled=%d/%d waitlist=%d/%s open=%s",
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
    course_label = os.getenv(
        "COURSE_LABEL", f"MATH 113 discussion {discussion_number}"
    ).strip()
    public_section_url = os.getenv("COURSE_URL") or os.getenv(
        "CALCENTRAL_PUBLIC_SECTION_URL_TEMPLATE", DEFAULT_PUBLIC_SECTION_URL_TEMPLATE
    ).format(discussion_number=discussion_number)
    lecture_mode = os.getenv("SECTION_COMPONENT", "DIS").upper() != "DIS"
    logger.info("opening the private CalCentral browser profile at %s", profile)

    alerted = False
    startup_sent = False
    session_cookies = []  # In memory only; never log or write these to a separate file.
    headless = True
    verifying_transfer = False
    keep_visible = False
    with sync_playwright() as playwright:
        context = None

        def launch(headless: bool):
            new_context = playwright.chromium.launch_persistent_context(
                str(profile), channel="chrome", headless=headless,
            )
            if session_cookies:
                new_context.add_cookies(session_cookies)
            return new_context

        try:
            context = launch(True)
            page = context.pages[0] if context.pages else context.new_page()
            while True:
                succeeded = False
                try:
                    status = fetch_calcentral_status(
                        page, enrollment_url=enrollment_url, term_name=term_name,
                        parent_class_number=section_id if lecture_mode else parent_class_number,
                        section_id=section_id, discussion_number=discussion_number,
                        waitlist_capacity=None,
                    )
                    alerted = False  # A successful read ends the sign-in incident.
                    verifying_transfer = False
                    if status.waitlist_capacity is None:
                        status = replace(status, waitlist_capacity=fetch_waitlist_capacity(
                            public_section_url, section_id,
                        ))
                    process_status(status, public_section_url, state_path)
                    if not startup_sent:
                        startup_sent = send_startup_notification("CalCentral", interval_seconds, continuous)
                    succeeded = True
                except SignInRequired as exc:
                    logger.warning("%s", exc)
                    if verifying_transfer:
                        keep_visible = True
                        verifying_transfer = False
                        logger.warning(
                            "Headless session transfer failed. Keeping Chrome open for this run; "
                            "you can minimize it once monitoring resumes."
                        )
                    if not alerted:
                        try:
                            send_signin_notification()
                            alerted = True
                        except MonitorError as delivery_error:
                            logger.error("%s Retrying next check.", delivery_error)
                            if not continuous:
                                return 1
                            time.sleep(interval_seconds)
                            continue
                    if headless:
                        session_cookies = context.cookies()
                        close_browser_context(context)
                        context = None
                        context = launch(False)
                        headless = False
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
                    complete_calcentral_login(page)
                    if not keep_visible:
                        session_cookies = context.cookies()
                        close_browser_context(context)
                        context = None
                        context = launch(True)
                        headless = True
                        verifying_transfer = True
                        page = context.pages[0] if context.pages else context.new_page()
                    else:
                        logger.info("Resuming in the signed-in Chrome window. You may minimize it.")
                    continue
                except MonitorError as exc:
                    logger.error("%s", exc)
                except PlaywrightError:
                    logger.error("CalCentral browser check failed; retrying if continuous mode is enabled.")
                if not continuous:
                    return 0 if succeeded else 1
                time.sleep(interval_seconds)
        except PlaywrightError as exc:
            raise MonitorError("Could not start or restore the CalCentral browser.") from exc
        finally:
            if context is not None:
                close_browser_context(context)


def fetch_waitlist_capacity(url: str, section_id: str) -> int | None:
    """Refresh Berkeley's waitlist maximum; never substitute enrollment capacity."""
    try:
        return determine_status(locate_section(fetch_page(url), section_id)).waitlist_capacity
    except MonitorError as exc:
        logger.warning("Waitlist capacity unavailable; displaying unknown (%s)", exc)
        return None


def parse_calcentral_card(text: str, section_id: str) -> SectionStatus:
    """Read the lecture popup's explicit availability, including its waitlist limit."""
    text = " ".join(text.split())
    identity = re.search(
        rf"\b(Open|Closed|Waitlist(?:ed)?)\s+(?:LEC|LAB|SEM|STD|REC|TUT|FLD|IND|WEB|WRK)\s+\d+\s+#{re.escape(section_id)}\s*/",
        text, re.I,
    )
    counts = re.search(
        r"Seat Availability\s+Open:\s*(\d+)\s+Capacity:\s*(\d+)\s+Waitlisted:\s*(\d+)\s*/\s*(\d+)",
        text, re.I,
    )
    if not identity or not counts:
        raise MonitorError(f"Class #{section_id} availability popup was not found.")
    available, capacity, waitlisted, maximum = map(int, counts.groups())
    description = identity.group(1).title()
    is_open = description.lower() == "open" and available > 0
    return SectionStatus(section_id, "O" if is_open else "C", description,
                         max(capacity - available, 0), capacity, waitlisted, maximum, 0, is_open)


def fetch_calcentral_detail(page: Any, section_id: str) -> SectionStatus:
    """Refresh a user-opened lecture detail page and verify its class identity."""
    page.reload(wait_until="domcontentloaded", timeout=60_000)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        for frame in page.frames:
            text = frame.locator("body").inner_text()
            # Require the class identity and labeled counts in the same frame.
            if not re.search(rf"\bClass\s*(?:Number|Nbr|#)\s*:?\s*{re.escape(section_id)}\b", text, re.I):
                continue
            try:
                return parse_calcentral_text(text, section_id)
            except MonitorError:
                pass
        page.wait_for_timeout(500)
    raise MonitorError(
        f"Open Enrollment Information for class #{section_id} in the monitoring "
        "browser. Its class number and labeled enrollment counts must be visible."
    )


def fetch_calcentral_status(
    page: Any,
    *,
    enrollment_url: str,
    term_name: str,
    parent_class_number: str,
    section_id: str,
    discussion_number: str,
    waitlist_capacity: int | None,
) -> SectionStatus:
    """Navigate the read-only PeopleSoft class search and extract one row."""
    logger.info("CalCentral: opening Enrollment Center")
    page.goto(enrollment_url, wait_until="domcontentloaded", timeout=60_000)
    logger.info("CalCentral: waiting for Berkeley sign-in redirects to finish")
    wait_for_enrollment_entry(page)
    logger.info("CalCentral: Enrollment Center loaded")

    search = page.locator("#CW_CLSRCH_WRK2_PTUN_KEYWORD")
    deadline = time.monotonic() + 30
    class_search_clicked = False
    term_clicked = False

    while time.monotonic() < deadline and search.count() != 1:
        require_signed_in(page)
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
            "seconds. Enrollment Center navigation did not finish; retrying "
            "does not require signing in unless a login screen appears."
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
        require_signed_in(page)
        for frame in page.frames:
            if os.getenv("SECTION_COMPONENT", "DIS").upper() != "DIS":
                try:
                    return parse_calcentral_card(frame.locator("body").inner_text(), section_id)
                except MonitorError:
                    continue
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
        waitlist_capacity,
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
    startup_sent = False
    try:
        while True:
            try:
                run_check(url, section_id, state_path)
                if not startup_sent:
                    startup_sent = send_startup_notification("Public Berkeley page", interval_seconds, True)
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
        default=int(os.getenv("CHECK_INTERVAL_SECONDS", "60")),
        metavar="SECONDS",
        help="continuous-mode interval (default: 60 or CHECK_INTERVAL_SECONDS)",
    )
    picker = parser.add_mutually_exclusive_group()
    picker.add_argument("--setup", action="store_true", help="open the startup class picker")
    picker.add_argument("--no-ui", action="store_true", help="use environment settings without a picker")
    args = parser.parse_args(argv)
    if args.interval < 30:
        parser.error("--interval must be at least 30 seconds")
    return args


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    args = parse_args(argv)
    if not args.test_notification and (args.setup or (not args.no_ui and sys.stdin.isatty())):
        from setup_ui import choose_course, apply_profile
        try:
            profile = choose_course(calcentral=args.calcentral, interval=args.interval)
            if profile is None:
                return 0
            apply_profile(profile)
            args.calcentral = profile["mode"] == "calcentral"
            args.continuous = profile["continuous"]
            args.interval = profile["interval"]
        except (MonitorError, OSError, ValueError) as exc:
            print(f"Setup failed: {exc}", file=sys.stderr)
            return 1
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
        send_startup_notification("Public Berkeley page", args.interval, False)
    except MonitorError as exc:
        logger.error("%s", exc)
        return 1
    except Exception:
        logger.exception("unexpected monitoring error")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")
        sys.exit(0)
