# Berkeley Section Monitor

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)

A small Python monitor for UC Berkeley discussion-section enrollment. It can
read Berkeley's public class page or a signed-in CalCentral/PeopleSoft session,
track changes over time, and notify you through Discord-compatible webhooks or
email.

> [!IMPORTANT]
> This project only monitors availability. It never enrolls in a class, adds a
> class to your cart, or bypasses CalNet and Duo authentication.

## Features

- Local HTML/CSS card dashboard in a desktop window
- System tray mode: close to hide, reopen from the tray, Quit to stop
- Add/remove classes and start/pause each monitor independently
- Saved watchlist, check intervals, and notification preferences
- Seat-opening, waitlist-opening, any-change, or muted alerts per class
- Two data sources: public Berkeley class pages and live CalCentral
- Notifications when enrollment, waitlist, or status values change
- Optional Discord mention on changes
- Persistent state to prevent duplicate change alerts
- One-shot and continuous monitoring modes
- Automated tests for parsing, notifications, and command-line behavior

## How it works

```text
Berkeley data source
        │
        ▼
Normalize enrollment and waitlist data
        │
        ▼
Compare with the last saved state
        │
        ├── No matching change ──► update dashboard quietly
        │
        └── Changed ────► notification + optional Discord mention
                              │
                              ▼
                       Save the new state
```

The monitor reports one of three user-facing states:

- **Open** — seats are available for immediate enrollment
- **Waitlist** — the section is full, but its waitlist is available
- **Closed** — neither enrollment nor the waitlist is available

## Choose a monitoring mode

| | Public page | CalCentral |
| --- | --- | --- |
| Data | Berkeley class page | Live PeopleSoft enrollment flow |
| Login | Not required | CalNet and Duo required |
| Browser | Not required | Headless Chrome; visible for sign-in |
| Best for | Schedulers and remote hosts | Local, up-to-date monitoring |
| Tradeoff | Public data may be cached | Computer must remain active; sign-in may need renewal |

Public-page mode is the easiest place to start. CalCentral mode is useful when
you need the freshest values and can keep a signed-in browser running.

## Quick start (Windows PowerShell)

Install Python 3.10+ and Microsoft Edge WebView2 Runtime (normally included on Windows),
then run these commands from the cloned folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` to add your Discord webhook URL and optional Discord user ID, or
use the email settings below. Existing environment variables take precedence.
The program loads `.env` automatically; no activation or manual import is needed.
Do not overwrite an existing `.env` when updating an installation.

After first-time setup, open the dashboard without typing a command:

- **Windows:** double-click `Start Monitor.bat` in the project folder.
- **VS Code:** open this folder, install Microsoft's Python and Python Debugger
  extensions if needed, then press **Ctrl+F5** (Run Without Debugging) or **F5**.
  Select **Start Berkeley Monitor** if prompted.
- You can also open `desktop.py` and click **Run Python File** in VS Code.
  If VS Code previously selected another interpreter, use **Python: Select
  Interpreter** once to select `.venv\Scripts\python.exe`.

The double-click launcher runs without a console. Existing saved classes are
imported on the first dashboard launch. Click **Add class** to paste a section
URL and choose its source, interval, and notification preference. Click **Start**
on a card or **Start all** to begin. New launches start paused so you control
when checks and notifications begin.

Updating an existing installation? Run the dependency install command above
once to add pywebview, pystray, and Pillow. Keep your existing `.env`.

## Dashboard and system tray

Availability is marked **Stale** when the last successful check is older than
twice the section's check interval (at least two minutes). Stale cards use muted
availability colors and are excluded from a course group's open-discussion count.
The warning clears after a successful check; failed checks do not refresh its age.
Returning to the window immediately refreshes the dashboard's view of monitoring.

Add the discussion sections that work for your schedule using **Add class**.
Two or more discussions for the same course and term automatically appear in
one course group, with shared public lecture details and individual counts,
check timers, settings, and alerts. Different associated lectures are shown
separately. **Start course / Pause course** controls just that group; **Add
discussion** reuses its source, interval, and alert preference for the next form.
Only sections you add are monitored. A seat-opening alert from any selected
discussion identifies that section; the first reading establishes its baseline.

Use a card's **··· → Move earlier / Move later** to reorder your watchlist.
The order is saved across launches and class edits. Moving cards does not restart
monitoring.

Discussion cards automatically show their associated lecture's status, enrollment,
and waitlist counts after a successful check. This extra context always comes from
Berkeley's public pages, even when the discussion uses CalCentral. The monitor
verifies the course, term, and lecture class number instead of guessing a parent.
If the lookup fails or is ambiguous, the discussion keeps working; previous lecture
counts are marked as last known. Alerts still track the discussion only.

The notification indicator opens Preferences and shows the configured delivery
channel and Discord mention setting. Configured means credentials are present,
not that delivery has been verified. Cards show actual alert delivery results.
Under **Preferences → Discord connection**, paste a webhook URL and optionally
your Discord user ID, then click **Save Discord settings**. Leave the URL blank
to keep the existing webhook; clear the user ID to turn mentions off. The app
updates only these two `.env` settings and restarts active monitors to apply them.
The saved webhook is never returned to the UI. **Send test notification** sends
a labeled test using saved settings and reports whether Discord accepted it.
Email configuration remains in `.env`.

Optional read-only public integration checks can be enabled with
`$env:RUN_LIVE_PUBLIC_TESTS='1'` before running pytest. They verify two discussion
pages resolve to their actual lecture and return valid enrollment data.

Each card shows enrollment, waitlist counts, the last check, and monitoring
state. A progress bar counts down to the next check (or retry). The countdown
starts after a check and notification delivery finish; while work is in progress,
the card says **Checking now**. Pausing or waiting for sign-in clears the timer.
**Pause** stops that class and closes its monitor browser; the last
known counts stay visible. **Settings** changes its interval, source, or alerts.
**Check now** skips the scheduled wait for that class. It is disabled during
an ongoing check or sign-in so checks cannot overlap. On a paused card it runs
one check and returns to paused; a failed check stays flagged for attention.
After a manual check, a running class starts a fresh normal countdown.

**Recent activity** shows the newest 200 events across your classes, saved
locally and filterable by class. It records successful checks, changes in
availability, delivered enrollment notifications, failed checks/delivery,
sign-in needs, and starts/pauses. Removing a class retains its past activity.
The log contains structured messages rather than raw exceptions or credentials.

Saving changes to a running class restarts only that monitor. Use the **···**
menu to open its Berkeley page or remove it. Checks are independent, so one
class waiting for sign-in does not hold up another.

Closing the dashboard hides it to the system tray, with a notice on the first
close. Double-click the tray icon to reopen it. Its menu includes **Open
dashboard**, **Start all**, **Pause all**, and **Quit**. **Quit** stops all
monitors and closes the app. If the tray cannot initialize, the dashboard
explains that it must remain open, and closing exits instead of hiding it.
Pausing or quitting can take a few seconds while browser work finishes.

The watchlist and preferences are stored in `.dashboard.json`. Credentials
remain in `.env` and are never passed to the HTML interface. The app uses
bundled HTML/CSS/JavaScript locally; it requires no website hosting or account.
The desktop/tray integration targets Windows. The original CLI remains available
on macOS/Linux; other desktop platforms need additional GUI dependencies.

### Enrollment notification controls

Set a default in **Preferences**, then optionally override it in each card's
**Settings**:

| Preference | When an alert is sent |
| --- | --- |
| Seat becomes available (default) | A known non-open section becomes open |
| Waitlist becomes available | A previously unavailable waitlist becomes available |
| Any enrollment or waitlist change | Status, enrollment, capacity, waitlist, or reserved seats change |
| Muted | No enrollment alerts; dashboard still updates |

The first reading establishes a baseline if no prior state exists. Desktop
mode sends no routine unchanged or startup webhook messages. Sign-in alerts
remain separate from enrollment preferences, including when muted. A failed
enrollment notification is retried after a successful check; the card still
shows the latest counts and flags the delivery problem. Webhook/email delivery
settings continue to come from `.env`; restart the app after editing that file.

### Original terminal picker

The Tkinter picker and CLI are still available for terminal use (Tk support required):

```powershell
.\.venv\Scripts\python.exe monitor.py --setup
```

1. Click **Find a class**, open an individual lecture or discussion page in
   Berkeley Class Search, and paste its URL into the picker.
2. Choose public-page or CalCentral monitoring. For CalCentral discussions,
   also enter the parent lecture's class number.
3. Choose the check interval (60 seconds by default, minimum 30) and whether
   to keep checking or run once.
4. Click **Start monitoring**. The program reads the class number and term
   from Berkeley and remembers the selection for next time.

The picker appears once per interactive launch, including continuous mode;
none of the repeated checks opens it again. Closing it cancels the launch.
Saved classes appear in a dropdown, with the last used class selected.
`--setup` explicitly opens it; `--no-ui` skips it for scripts and schedulers.
A selected profile overrides `.env` course settings and gets its own state file.
Notification settings remain in `.env`.

For classes without discussions (such as a lecture-only MATH 104 or 185),
paste the **lecture** page URL. Public mode reads the lecture directly.
CalCentral lecture mode searches the lecture class number and reads its
**Class Information** popup automatically, including the explicit waitlist limit.

Enrollment counts and waitlist limits are automatic. CalCentral uses the lecture popup's explicit limit when available and otherwise refreshes public waitlist metadata; if unavailable, the limit is
shown as `unknown` and retried next time. Lecture detail pages can supply their
own explicit waitlist capacity. There is no need to enter `WAITLIST_CAPACITY`.

Keep the terminal open while monitoring; press **Ctrl+C** to stop. CalCentral
also requires Google Chrome installed. Routine checks use a headless browser.
The interval is the delay **after** each check; request and page-load time adds
to it. Faster public checks may still see cached Berkeley data. Existing
`CHECK_INTERVAL_SECONDS` settings and saved profiles override the 60-second default.

On macOS/Linux, create the environment with `python3 -m venv .venv`, install
with `.venv/bin/python -m pip install -r requirements.txt`, and run
`.venv/bin/python monitor.py --setup`. Linux may need its `python3-tk` package.
Public-page fallback requires `curl` on the system path.

To run directly from `.env` without the picker:

```powershell
.\.venv\Scripts\python.exe monitor.py --no-ui --continuous --interval 30
.\.venv\Scripts\python.exe monitor.py --test-notification
```

For direct configuration, set `COURSE_URL`, `SECTION_ID`, `COURSE_LABEL`,
`DISCUSSION_NUMBER` (the component number), and `SECTION_COMPONENT=LEC` for a
lecture (`DIS` is the legacy default). CalCentral discussion mode also uses
`CALCENTRAL_PARENT_CLASS_NUMBER` and `CALCENTRAL_TERM`.

## Monitor multiple classes

Open the dashboard using `Start Monitor.bat` or VS Code. Add each class once,
then use **Start all** or each card's **Start** button. The watchlist is remembered
between launches. You can add another class while the others keep running.

Each class keeps its own interval, data source, notifications, and saved state.
Classes run independently. A failed class does not stop the others. Use
**Pause all** to stop checks or **Quit** to exit. The legacy terminal picker
also supports an **Add to monitoring list** queue; Ctrl+C stops that CLI group.

CalCentral uses a separate persistent Chrome profile for each class in a
multi-class run. You may need to sign in and complete Duo for each class on
its first run; these sessions are saved for reuse. Multiple CalCentral classes
also use more memory because each runs its own browser.

## CalCentral mode

Run one live check:

```sh
.venv/bin/python monitor.py --calcentral
```

The monitor first tries the private saved Chrome profile in headless mode.
If CalCentral requires authentication:

1. Monitoring pauses and a Discord-compatible webhook alert is sent.
2. A visible Chrome window opens. Sign in with CalNet and complete Duo.
3. The monitor opens Enrollment Center automatically. Complete any additional
   Duo verification it requests, even if Academics was already signed in.
4. Once Enrollment Center loads, monitoring resumes automatically in the
   background. You do not need to press Enter in the terminal.

Set both `NOTIFICATION_WEBHOOK_URL` and `DISCORD_USER_ID` in `.env` to get
an actual Discord mention. With only the webhook, the message is posted without
a mention. The alert contains no cookies, credentials, or session URLs.
One successful alert is sent per sign-in incident during a run; successful
class retrieval resets suppression for a future expiration. Restarting the
monitor resets suppression. Failed delivery is retried at the check interval
in continuous mode. These alerts do not change saved enrollment state.

Only explicit sign-in/expired-session screens trigger a sign-in alert. Ordinary
network and parsing errors are logged separately. No real Discord messages are
sent by the automated tests.

The session stays in `.calcentral-browser-profile/`; keep that directory private.
The terminal and computer must stay running. Use Ctrl+C to stop. Headless session
reuse depends on Berkeley's authentication behavior. The monitor transfers
session cookies in memory when switching between visible and headless Chrome.
If the first headless check still requires authentication, it falls back to
keeping Chrome visible for that run instead of repeatedly switching modes.
You can minimize this fallback window, but leave it open. Duplicate Discord
pings remain suppressed during the same sign-in incident.

```sh
.venv/bin/python monitor.py --calcentral --continuous
```

## Commands

| Command | Description |
| --- | --- |
| `.venv/bin/python monitor.py` | Run one public-page check |
| `.venv/bin/python monitor.py --continuous` | Repeat public-page checks |
| `.venv/bin/python monitor.py --calcentral` | Run one live CalCentral check |
| `.venv/bin/python monitor.py --calcentral --continuous` | Repeat live CalCentral checks |
| `.venv/bin/python monitor.py --test-notification` | Send a harmless delivery test |
| `.venv/bin/python monitor.py --help` | Show all command-line options |
| `.venv/bin/python -m pytest -q` | Run the test suite |

Use `--interval SECONDS` to change the continuous-mode interval. The default is
60 seconds and the minimum is 30 seconds:

```sh
.venv/bin/python monitor.py --calcentral --continuous --interval 120
```

The delay begins after each completed check, so timestamps can drift by the
few seconds a request or browser search takes.

## Notifications

The desktop dashboard follows the notification preferences described above.
The following startup and routine-message behavior applies to the legacy CLI.
After the first successful check of each CLI run, the monitor posts a startup
message with the selected class, source, and interval. This message explicitly
disables all Discord mentions. Continuous checks do not repeat it; failed
delivery is retried after the next successful check.

### Discord or another compatible webhook

Create a Discord webhook under **Channel Settings → Integrations → Webhooks**,
then set:

```dotenv
NOTIFICATION_WEBHOOK_URL=https://discord.com/api/webhooks/REPLACE_ME
DISCORD_USER_ID=123456789012345678
```

`DISCORD_USER_ID` is optional. When present, the user is mentioned only after a
status, enrollment, waitlist, capacity, or reserved-seat value changes. Every
successful check still posts a routine webhook update.

If delivery fails, the new state is not saved, allowing the next check to retry
the notification.

### Email

When `NOTIFICATION_WEBHOOK_URL` is unset, the monitor can use SMTP instead:

```dotenv
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_FROM=sender@example.com
NOTIFY_EMAIL=recipient@example.com
SMTP_USERNAME=sender@example.com
SMTP_PASSWORD=REPLACE_ME
```

`SMTP_FROM` and `NOTIFY_EMAIL` are required when `SMTP_HOST` is set. Username
and password are optional when the provider does not require authentication.
Unlike webhook mode, email is sent only when a change is detected.

## Configuration reference

<details>
<summary><strong>Course and section settings</strong></summary>

| Variable | Default | Purpose |
| --- | --- | --- |
| `COURSE_LABEL` | `MATH 113 discussion <DISCUSSION_NUMBER>` | Human-readable notification label |
| `SECTION_COMPONENT` | `DIS` | `DIS` for automatic discussion search; `LEC` for guided lecture details |
| `DISCUSSION_NUMBER` | `104` | Component number shown in PeopleSoft |
| `SECTION_ID` | Optional in public mode; `27743` fallback in CalCentral mode | Five-digit discussion class number |
| `COURSE_URL` | Built-in MATH 113 URL | Exact page read in public mode |
| `CALCENTRAL_PARENT_CLASS_NUMBER` | `22491` | Parent lecture class number searched in PeopleSoft |
| `CALCENTRAL_TERM` | `2026 Fall` | Term link text in PeopleSoft |
| `CALCENTRAL_PUBLIC_SECTION_URL_TEMPLATE` | Built-in Fall 2026 MATH 113 template | Public URL used to find the true waitlist maximum; supports `{discussion_number}` |

</details>

<details>
<summary><strong>Runtime, files, and notifications</strong></summary>

| Variable | Default | Purpose |
| --- | --- | --- |
| `CHECK_INTERVAL_SECONDS` | `60` | Delay between continuous checks |
| `STATE_FILE` | Mode-specific file | Override the saved-state location |
| `LOG_LEVEL` | `INFO` | Python log level |
| `CALCENTRAL_PROFILE_DIR` | `.calcentral-browser-profile` | Persistent Chrome-profile directory |
| `NOTIFICATION_WEBHOOK_URL` | Unset | Discord, Slack, or compatible webhook URL |
| `DISCORD_USER_ID` | Unset | Discord account mentioned on changes |
| `SMTP_HOST` | Unset | SMTP server, used only without a webhook |
| `SMTP_PORT` | `587` | SMTP TLS port |
| `SMTP_FROM` | Unset | Sender email address |
| `NOTIFY_EMAIL` | Unset | Recipient email address |
| `SMTP_USERNAME` | Unset | Optional SMTP login |
| `SMTP_PASSWORD` | Unset | Optional SMTP password or app password |

Without the picker, state defaults to `.section_status.json` in public mode and a
`.calcentral_section_status_<class-number>.json` file in CalCentral mode. Give
each simultaneous monitor its own state file to avoid false change alerts.

</details>

<details>
<summary><strong>Advanced URL settings</strong></summary>

| Variable | Purpose |
| --- | --- |
| `CALCENTRAL_START_URL` | Initial page displayed for CalNet login |
| `CALCENTRAL_ENROLLMENT_URL` | Direct PeopleSoft Enrollment Center entry point |

Most users should leave these values unchanged.

</details>

## Scheduling public-page checks

Public mode works well as a one-shot scheduled task. For example, this cron
entry runs it every five minutes:

```cron
*/5 * * * * cd "/absolute/path/to/Berkeley-Section-Monitor" && .venv/bin/python monitor.py --no-ui >> monitor.log 2>&1
```

Use absolute paths because cron has a smaller environment than an interactive
terminal. CalCentral mode is not suitable for cron or GitHub Actions because
it occasionally requires interactive CalNet/Duo login.

GitHub Actions can run public mode, but runner files disappear after each job.
Store state externally if change suppression must persist between runs.

## Monitoring another course

Collect these values from Berkeley Class Search:

1. Academic term
2. Course and discussion label
3. Discussion number
4. Five-digit discussion class number
5. Five-digit parent lecture class number
6. Exact public section URL

For example:

```dotenv
COURSE_LABEL=STAT 134 discussion 104
DISCUSSION_NUMBER=104
SECTION_ID=20742
CALCENTRAL_PARENT_CLASS_NUMBER=20746
CALCENTRAL_TERM=2027 Spring

COURSE_URL=https://classes.berkeley.edu/content/2027-spring-stat-134-104-dis-104
CALCENTRAL_PUBLIC_SECTION_URL_TEMPLATE=https://classes.berkeley.edu/content/2027-spring-stat-134-{discussion_number}-dis-{discussion_number}
```

Do not confuse the discussion number with its five-digit class number or the
parent lecture's class number.

CalCentral discussions use automatic component-row search. Lectures use the
Class Information popup; public mode reads each component from its section URL.

### Monitoring multiple sections

Run one process per section. Each process needs its own configuration and state
file. Simultaneous CalCentral processes must also use different
`CALCENTRAL_PROFILE_DIR` values—never point two Playwright processes at the
same browser-profile directory.

## Troubleshooting

<details>
<summary><strong>CalCentral opens, but nothing happens</strong></summary>

Read the latest `CalCentral:` progress line in the terminal. A normal run opens
Enrollment Center, selects the configured term, searches the parent class
number, and finds the discussion row. PeopleSoft can take several seconds at
each step.

If a search field never appears or the session-expired message is shown, stop
the monitor, sign in again, wait for CalCentral Academics to load, and restart.

</details>

<details>
<summary><strong>The discussion class number was not found</strong></summary>

Confirm that these three values belong to the same course:

```dotenv
DISCUSSION_NUMBER=106
SECTION_ID=27745
CALCENTRAL_PARENT_CLASS_NUMBER=22491
```

Open the parent lecture manually and verify that its **Discussion Section**
table contains the expected discussion and class number.

</details>

<details>
<summary><strong>The public page returns HTTP 403 or missing JSON</strong></summary>

For HTTP 403 responses, the monitor automatically retries with `curl`. Check
that it is installed with `curl --version`.

Missing Drupal enrollment JSON usually means Berkeley changed the page or
`COURSE_URL` does not point to an individual section. Open the URL and confirm
that it is the intended section page.

</details>

<details>
<summary><strong>The waitlist appears as 0/40 in PeopleSoft</strong></summary>

PeopleSoft pairs the current waitlisted count with enrollment capacity instead
of the true waitlist maximum. The monitor normally gets the correct maximum
from the matching public page. If that lookup fails, the monitor shows `unknown` for the maximum and retries
on the next check. Verify `COURSE_URL` points to the exact section; the
legacy `CALCENTRAL_PUBLIC_SECTION_URL_TEMPLATE` is used only when it is unset.

</details>

<details>
<summary><strong>A configuration correction triggers a false change</strong></summary>

The saved state still contains the previous interpretation. Stop the monitor,
remove only that section's generated state file, and restart to establish a
new baseline. If you set `STATE_FILE`, ensure it is unique to that section.

</details>

<details>
<summary><strong>A webhook notification fails</strong></summary>

Confirm that the webhook URL is complete, the webhook still exists, the
machine is online, and the target channel allows posts. Then run:

```sh
.venv/bin/python monitor.py --test-notification
```

</details>

## Project structure

| Path | Purpose |
| --- | --- |
| [`desktop.py`](./desktop.py) | Local webview, tray integration, and dashboard entry point |
| [`desktop_backend.py`](./desktop_backend.py) | Saved watchlist and worker lifecycle |
| [`desktop_worker.py`](./desktop_worker.py) | Isolated monitor with status events and stop/sign-in commands |
| [`desktop_ui/`](./desktop_ui/) | Dashboard HTML, CSS, and JavaScript |
| [`setup_ui.py`](./setup_ui.py) | Legacy terminal picker and course discovery |
| [`.env.example`](./.env.example) | Notification configuration template |
| [`monitor.py`](./monitor.py) | Fetching, parsing, state tracking, browser automation, notifications, and CLI |
| [`tests/`](./tests/) | Automated checks for monitoring, authentication, and the desktop interface |
| [`requirements.txt`](./requirements.txt) | Python dependencies |
| [`.gitignore`](./.gitignore) | Prevents credentials, sessions, and generated state from entering Git |

Generated files such as `.env`, `.venv/`, `.calcentral-browser-profile/`, and
`.monitor-profiles.json`, `.monitor-state/`, and section-state JSON files stay local and are ignored by Git.
Dashboard state (`.dashboard.json`), its instance lock, and `.desktop-qa/` screenshots
are also ignored.

## Desktop verification

See [Enrollment automation feasibility review](docs/enrollment-automation-research.md)
for the separate research on Chrome extensions and Berkeley APIs. The app
remains a monitor and does not submit enrollment changes.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
$env:RUN_DESKTOP_UI_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_desktop_ui.py -q
$env:RUN_DESKTOP_NATIVE_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_desktop_native.py -q
```

Browser QA uses installed Chrome and fixture classes; screenshots go to
`.desktop-qa/`. Native QA briefly opens an isolated Windows webview and tray
icon, tests the bridge and hide/quit behavior, then closes. These tests do not
access real enrollment data, sign into Berkeley, or send notifications.

## Security

- Enter CalNet credentials only on Berkeley's genuine authentication page.
- Never commit webhook URLs, SMTP passwords, cookies, or `.env` files.
- Never publish `.calcentral-browser-profile/`; it contains session data.
- Revoke and replace a webhook immediately if it is exposed.
- Public-page mode does not need CalNet credentials.
