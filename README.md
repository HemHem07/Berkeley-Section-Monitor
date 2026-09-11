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
        ├── No change ──► routine webhook update without a mention
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
| Browser | Not required | Visible Chrome window |
| Best for | Schedulers and remote hosts | Local, up-to-date monitoring |
| Tradeoff | Public data may be cached | Session and computer must remain active |

Public-page mode is the easiest place to start. CalCentral mode is useful when
you need the freshest values and can keep a signed-in browser running.

## Quick start

### 1. Install

Clone the repository, enter it, and create a virtual environment:

```sh
git clone https://github.com/HemHem07/Berkeley-Section-Monitor.git
cd Berkeley-Section-Monitor
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Requirements: Python 3.10 or newer, plus Google Chrome when using CalCentral
mode. The public-page fallback also expects `curl`, which is included on macOS
and many Linux systems.

### 2. Configure a section

Create a local `.env` file:

```dotenv
COURSE_LABEL=MATH 113 discussion 104
DISCUSSION_NUMBER=104
SECTION_ID=27743
COURSE_URL=https://classes.berkeley.edu/content/2026-fall-math-113-104-dis-104

CALCENTRAL_PARENT_CLASS_NUMBER=22491
CALCENTRAL_TERM=2026 Fall

NOTIFICATION_WEBHOOK_URL=https://discord.com/api/webhooks/REPLACE_ME
DISCORD_USER_ID=123456789012345678

CHECK_INTERVAL_SECONDS=300
LOG_LEVEL=INFO
```

Replace the example values with the class, discussion, and notification
details you want to use. Do not add spaces around `=`.

> [!CAUTION]
> `.env` can contain webhook URLs, email passwords, and other secrets. It is
> ignored by Git and must never be committed or shared.

### 3. Load the configuration

```sh
set -a
. ./.env
set +a
```

Reload it whenever you open a new terminal or edit `.env`.

### 4. Test and run

```sh
# Send a harmless test notification
.venv/bin/python monitor.py --test-notification

# Check the public page once
.venv/bin/python monitor.py

# Keep checking the public page
.venv/bin/python monitor.py --continuous
```

The test notification does not modify the saved section state and clearly
identifies itself as a test.

## CalCentral mode

Run one live check:

```sh
.venv/bin/python monitor.py --calcentral
```

On the first run:

1. A dedicated Chrome window opens.
2. Sign in on Berkeley's genuine CalNet page and complete Duo.
3. Wait for the CalCentral Academics page to load.
4. Return to the terminal and press <kbd>Enter</kbd>.

The monitor opens Enrollment Center, searches for the configured parent
lecture, finds the matching discussion row, and reads its enrollment data. It
does not select the discussion or click **Enroll or Add to Cart**.

The browser session is kept in `.calcentral-browser-profile/`. Keep that
directory private. If the session expires, stop the monitor, sign in again in
the monitoring browser, and restart it.

To keep monitoring until you press <kbd>Ctrl</kbd>+<kbd>C</kbd>:

```sh
.venv/bin/python monitor.py --calcentral --continuous
```

You can minimize the Chrome window, but do not close it. The terminal, browser,
computer, and internet connection must remain active.

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
300 seconds and the minimum is 30 seconds:

```sh
.venv/bin/python monitor.py --calcentral --continuous --interval 120
```

The delay begins after each completed check, so timestamps can drift by the
few seconds a request or browser search takes.

## Notifications

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
| `DISCUSSION_NUMBER` | `104` | Component number shown in PeopleSoft |
| `SECTION_ID` | Optional in public mode; `27743` fallback in CalCentral mode | Five-digit discussion class number |
| `COURSE_URL` | Built-in MATH 113 URL | Exact page read in public mode |
| `CALCENTRAL_PARENT_CLASS_NUMBER` | `22491` | Parent lecture class number searched in PeopleSoft |
| `CALCENTRAL_TERM` | `2026 Fall` | Term link text in PeopleSoft |
| `CALCENTRAL_PUBLIC_SECTION_URL_TEMPLATE` | Built-in Fall 2026 MATH 113 template | Public URL used to find the true waitlist maximum; supports `{discussion_number}` |
| `WAITLIST_CAPACITY` | Unset | Manual waitlist-maximum fallback |

</details>

<details>
<summary><strong>Runtime, files, and notifications</strong></summary>

| Variable | Default | Purpose |
| --- | --- | --- |
| `CHECK_INTERVAL_SECONDS` | `300` | Delay between continuous checks |
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

State defaults to `.section_status.json` in public mode and a
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
*/5 * * * * cd "/absolute/path/to/Berkeley-Section-Monitor" && set -a && . ./.env && set +a && .venv/bin/python monitor.py >> monitor.log 2>&1
```

Use absolute paths because cron has a smaller environment than an interactive
terminal. CalCentral mode is not suitable for cron or GitHub Actions because
it requires a visible browser and interactive login.

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

The CalCentral automation currently expects a heading named **Discussion
Section**. Courses using laboratory, studio, quiz, or another component label
require a small code change.

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
from the matching public page. If that lookup fails, verify
`CALCENTRAL_PUBLIC_SECTION_URL_TEMPLATE` or temporarily set:

```dotenv
WAITLIST_CAPACITY=6
```

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
| [`monitor.py`](./monitor.py) | Fetching, parsing, state tracking, browser automation, notifications, and CLI |
| [`test_monitor.py`](./test_monitor.py) | Automated test suite |
| [`requirements.txt`](./requirements.txt) | Python dependencies |
| [`.gitignore`](./.gitignore) | Prevents credentials, sessions, and generated state from entering Git |

Generated files such as `.env`, `.venv/`, `.calcentral-browser-profile/`, and
section-state JSON files stay local and are ignored by Git.

## Security

- Enter CalNet credentials only on Berkeley's genuine authentication page.
- Never commit webhook URLs, SMTP passwords, cookies, or `.env` files.
- Never publish `.calcentral-browser-profile/`; it contains session data.
- Revoke and replace a webhook immediately if it is exposed.
- Public-page mode does not need CalNet credentials.
