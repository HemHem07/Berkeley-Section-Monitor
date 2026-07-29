# Berkeley section monitor

A configurable Python monitor for UC Berkeley discussion-section enrollment.
It can use either Berkeley's public class page or a signed-in
CalCentral/PeopleSoft browser session, save the previous result, and send
Discord webhook or email notifications.

The current defaults monitor:

- Course: MATH 113, Fall 2026
- Parent lecture class number: `22491`
- Discussion: `104`
- Discussion class number: `27743`
- Public page:
  <https://classes.berkeley.edu/content/2026-fall-math-113-104-dis-104>
- Check interval: 300 seconds (five minutes)
- Displayed notification time zone: Berkeley/Pacific time

## How the system works

```text
Berkeley data source
        |
        v
Normalize status, enrollment, and waitlist
        |
        v
Compare with the section's saved JSON state
        |
        +-- unchanged --> send routine webhook without a Discord ping
        |
        `-- changed ----> send notification and ping configured Discord user
                              |
                              v
                     save the new state
```

The monitor recognizes these user-facing statuses:

- **Open — seats available for immediate enrollment**
- **Waitlist — section full; waitlist available**
- **Closed — section and waitlist unavailable**

A change means any change to status, enrolled count, enrollment capacity,
waitlisted count, waitlist capacity, or open reserved-seat count. With a
Discord webhook configured, every successful check sends a message, but the
configured Discord user is mentioned only when one of those values changes.

## The two monitoring modes

### Public-page mode

Public mode requests the `classes.berkeley.edu` section page and parses
structured JSON embedded in:

```text
script[data-drupal-selector="drupal-settings-json"]
```

The relevant record is at `ucb.enrollment.available`. It provides the status,
enrolled count, enrollment capacity, waitlisted count, configured waitlist
maximum, and reserved-seat information.

Advantages:

- No CalNet login or browser required
- Works as a one-time command
- Suitable for cron, PythonAnywhere, and similar schedulers
- Easier to deploy

Limitations:

- Berkeley's public page can be cached and may lag behind PeopleSoft
- Berkeley occasionally returns HTTP 403 to Python Requests; the monitor
  automatically retries using the system `curl` command

### CalCentral mode

CalCentral mode launches a visible Chrome window using Playwright and a
dedicated persistent profile. After you complete CalNet and Duo yourself, the
monitor performs this read-only flow:

1. Open Enrollment Center.
2. Open **Class Search and Enroll**.
3. Select the configured term when necessary.
4. Search for the parent lecture's five-digit class number.
5. Open its **Class Information** results.
6. Scroll to **Discussion Section**.
7. Find the row matching the configured discussion number and class number.
8. Read status, open seats, enrollment capacity, and current waitlisted count.

It does not select the discussion radio button and does not click **Enroll or
Add to Cart**.

PeopleSoft displays discussion waitlists in a confusing format such as
`0 / 40`, where `40` is the section's enrollment capacity rather than its true
waitlist maximum. The monitor gets the live numerator from PeopleSoft and
automatically obtains the real `maxWaitlist` value from the matching public
section page.

Advantages:

- Uses the live enrollment workflow
- Usually more current than the public page

Limitations:

- Chrome and the Terminal process must stay open
- The computer must remain awake and online
- CalNet/PeopleSoft sessions eventually expire
- Duo may be required again
- Intended primarily for local continuous monitoring
- Not suitable for GitHub Actions because login is interactive

## Project components

| Path | Purpose |
| --- | --- |
| `monitor.py` | Fetching, browser automation, parsing, state comparison, notifications, logging, and CLI |
| `requirements.txt` | Python dependencies: Requests, Beautiful Soup, and Playwright |
| `test_monitor.py` | Automated tests for parsing, changes, notifications, and CLI behavior |
| `.env` | Your local configuration and credentials; never commit or share it |
| `.gitignore` | Prevents credentials, browser sessions, virtual environments, and state files from entering Git |
| `.venv/` | Project-specific Python environment |
| `.calcentral-browser-profile/` | Local Chrome profile containing the CalCentral session |
| `.section_status.json` | Default saved state for public mode |
| `.calcentral_section_status_<class-number>.json` | Default saved state for a CalCentral section |

The state files are generated automatically. They contain enrollment values,
not passwords. Removing the appropriate state file resets the baseline, so the
next successful check is treated as the first observation.

## Requirements

- macOS, Linux, or another environment with Python 3.10 or newer
- Google Chrome for CalCentral mode
- `curl` recommended as the public-mode HTTP fallback
- A Discord webhook or SMTP account for notifications
- CalNet and Duo access for CalCentral mode

## Setup from scratch

Open Terminal and enter the project directory:

```sh
cd "/Users/hem/Documents/Code Stuff/personal/Math 113 Discussion Scraper"
```

Create a virtual environment and install the dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

If `.env.example` is present, copy it:

```sh
cp .env.example .env
```

Otherwise create `.env` manually with a text editor. Do not add spaces around
the `=` signs and do not wrap ordinary numeric IDs in angle brackets.

Example configuration for MATH 113 Discussion 106:

```dotenv
COURSE_LABEL=MATH 113 discussion 106
DISCUSSION_NUMBER=106
SECTION_ID=27745
CALCENTRAL_PARENT_CLASS_NUMBER=22491
CALCENTRAL_TERM=2026 Fall

NOTIFICATION_WEBHOOK_URL=https://discord.com/api/webhooks/REPLACE_ME
DISCORD_USER_ID=123456789012345678

CHECK_INTERVAL_SECONDS=300
LOG_LEVEL=INFO
```

Load `.env` into the current shell:

```sh
set -a
. ./.env
set +a
```

Run those three commands again whenever you open a new Terminal or edit
`.env`. You do not need to close the whole Terminal.

## Discord notification setup

1. In Discord, open the target server and channel settings.
2. Open **Integrations → Webhooks**.
3. Create a webhook and copy its URL.
4. Put the URL in `NOTIFICATION_WEBHOOK_URL`.
5. To enable change pings, enable Discord Developer Mode, copy your numeric
   user ID, and put it in `DISCORD_USER_ID`.

Never commit the webhook URL. Anyone possessing it can post through that
webhook.

Notification behavior:

- Every successful webhook check posts the current status and Pacific time.
- Unchanged checks do not mention the Discord user.
- Status, enrollment, or waitlist changes mention the configured user.
- A failed notification does not replace the saved state, allowing the next
  check to retry the notification.

Test delivery without modifying the section state:

```sh
.venv/bin/python monitor.py --test-notification
```

The test explicitly says it is a test and does not claim that a seat opened.

## Optional email notification setup

If `NOTIFICATION_WEBHOOK_URL` is unset, the monitor can use SMTP:

```dotenv
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_FROM=sender@example.com
NOTIFY_EMAIL=recipient@example.com
SMTP_USERNAME=sender@example.com
SMTP_PASSWORD=REPLACE_ME
```

`SMTP_FROM` and `NOTIFY_EMAIL` are required when `SMTP_HOST` is set.
`SMTP_USERNAME` and `SMTP_PASSWORD` are used when the SMTP provider requires
authentication.

Unlike webhook mode, email is sent only when the monitor detects a change.

## CalNet authentication

The monitor does not store your CalNet password, Duo code, or a manually copied
cookie.

On the first CalCentral run:

1. A dedicated Chrome window opens.
2. Sign in on Berkeley's genuine CalNet page.
3. Complete Duo.
4. Wait for the CalCentral Academics page.
5. Return to Terminal and press Enter.

The session is retained locally in `.calcentral-browser-profile/`. Keep this
directory private. If Berkeley expires the session, stop the monitor with
`Ctrl+C`, sign in again in the monitoring Chrome window, restart the command,
and press Enter after CalCentral loads.

`CALCENTRAL_COOKIE` is not used. A copied cookie is incomplete, expires, and is
less safe than the dedicated browser-profile approach.

## Commands

### Test the notification

```sh
.venv/bin/python monitor.py --test-notification
```

### Run one public-page check

```sh
.venv/bin/python monitor.py
```

### Run public-page mode continuously

```sh
.venv/bin/python monitor.py --continuous
```

### Run one live CalCentral check

```sh
.venv/bin/python monitor.py --calcentral
```

Chrome remains interactive for login, the monitor performs one search, and
then it exits.

### Run live CalCentral checks continuously

```sh
.venv/bin/python monitor.py --calcentral --continuous
```

Press `Ctrl+C` to stop. You may minimize the monitoring Chrome window, but do
not close it.

### Change the interval

```sh
.venv/bin/python monitor.py --calcentral --continuous --interval 120
```

The minimum allowed interval is 30 seconds. The default is 300 seconds.

Continuous mode sleeps for the interval after each completed check. Because a
CalCentral search normally takes several seconds, completion timestamps drift
by that processing time. For example, a five-minute interval may produce
messages 5 minutes and 8 seconds apart. This does not mean checks are being
missed.

### Show command help

```sh
.venv/bin/python monitor.py --help
```

### Run the automated tests

```sh
.venv/bin/python -m pytest -q test_monitor.py
```

## Command-line options

| Option | Meaning |
| --- | --- |
| No option | Run one public-page check and exit |
| `--continuous` | Repeat checks until `Ctrl+C` |
| `--calcentral` | Use the signed-in CalCentral/PeopleSoft workflow |
| `--test-notification` | Send a harmless test notification and exit |
| `--interval SECONDS` | Set the continuous interval; minimum 30 seconds |

## Environment-variable reference

### Course and section

| Variable | Default | Purpose |
| --- | --- | --- |
| `COURSE_LABEL` | `MATH 113 discussion <DISCUSSION_NUMBER>` | Human-readable Discord notification label |
| `DISCUSSION_NUMBER` | `104` | Displayed component number, such as `104` or `106` |
| `SECTION_ID` | Public mode: optional; CalCentral: `27743` | Five-digit PeopleSoft discussion class number |
| `COURSE_URL` | MATH 113 Discussion 104 public URL | Exact public page used by public mode |
| `CALCENTRAL_PARENT_CLASS_NUMBER` | `22491` | Five-digit parent lecture class number searched in PeopleSoft |
| `CALCENTRAL_TERM` | `2026 Fall` | PeopleSoft term link text |
| `CALCENTRAL_PUBLIC_SECTION_URL_TEMPLATE` | Fall 2026 MATH 113 template | Public URL template used to obtain the true waitlist maximum; supports `{discussion_number}` |
| `WAITLIST_CAPACITY` | Unset | Optional manual override when public metadata is unavailable |

### Runtime and files

| Variable | Default | Purpose |
| --- | --- | --- |
| `CHECK_INTERVAL_SECONDS` | `300` | Continuous interval unless `--interval` is supplied |
| `STATE_FILE` | `.section_status.json` or a CalCentral class-specific file | Override the saved-state location |
| `LOG_LEVEL` | `INFO` | Python log level, such as `DEBUG`, `INFO`, or `WARNING` |
| `CALCENTRAL_PROFILE_DIR` | `.calcentral-browser-profile` | Persistent Chrome-profile directory |

If `STATE_FILE` is explicitly shared by several courses or modes, one course
can be compared against another course's previous values and create a false
change notification. Prefer the generated per-class defaults or give each
monitor its own state path.

### Notification credentials

| Variable | Default | Purpose |
| --- | --- | --- |
| `NOTIFICATION_WEBHOOK_URL` | Unset | Discord-, Slack-, or compatible webhook URL |
| `DISCORD_USER_ID` | Unset | Numeric Discord account ID mentioned only on changes |
| `SMTP_HOST` | Unset | SMTP server; used only when no webhook is configured |
| `SMTP_PORT` | `587` | SMTP TLS port |
| `SMTP_FROM` | Unset | Sender address |
| `NOTIFY_EMAIL` | Unset | Recipient address |
| `SMTP_USERNAME` | Unset | Optional SMTP login name |
| `SMTP_PASSWORD` | Unset | Optional SMTP password or app password |

### Advanced URL overrides

| Variable | Default | Purpose |
| --- | --- | --- |
| `CALCENTRAL_START_URL` | `https://calcentral.berkeley.edu/academics` | Initial page displayed for CalNet login |
| `CALCENTRAL_ENROLLMENT_URL` | Berkeley Enrollment Center URL | Direct PeopleSoft Enrollment Center entry point |

Most users should not change the advanced URLs.

## Scheduling public mode

Public mode is designed for one-shot scheduled execution. Open the cron editor:

```sh
crontab -e
```

Example five-minute entry:

```cron
*/5 * * * * cd "/absolute/path/to/project" && set -a && . ./.env && set +a && "/absolute/path/to/project/.venv/bin/python" monitor.py >> monitor.log 2>&1
```

Use absolute paths. Cron has a smaller environment than your interactive
Terminal.

CalCentral mode should not be run through cron because it needs a visible
browser, an interactive CalNet/Duo login, and a persistent process.

For PythonAnywhere or another scheduler, run the same one-shot public command.
GitHub Actions can run public mode, but ordinary runner files disappear after
the job. Persist the state externally if duplicate-change suppression must
survive between jobs.

## Troubleshooting

### `ModuleNotFoundError: No module named 'playwright'`

Install the project requirements:

```sh
.venv/bin/pip install -r requirements.txt
```

### CalCentral opens, but the monitor does nothing

Read the latest `CalCentral:` progress line in Terminal. A live check can take
several seconds while PeopleSoft loads and searches.

Expected sequence:

```text
CalCentral: opening Enrollment Center
CalCentral: Enrollment Center loaded
CalCentral: opening Class Search and Enroll
CalCentral: selecting term 2026 Fall
CalCentral: searching parent class number 22491
CalCentral: found Discussion 106, class number 27745
```

### `CalCentral class-number search field did not appear`

Likely causes:

- CalNet or PeopleSoft session expired
- Wrong term
- PeopleSoft is temporarily slow or unavailable
- Berkeley changed the Enrollment Center interface

Stop with `Ctrl+C`, sign in again, wait for Academics to load, and restart.

### `Discussion class number ... was not found`

Verify that all three identifiers belong together:

```dotenv
DISCUSSION_NUMBER=106
SECTION_ID=27745
CALCENTRAL_PARENT_CLASS_NUMBER=22491
```

Open the parent class manually and confirm that the Discussion Section table
contains the expected row, for example `106 #27745`.

### Popup: `Please enter at least one search filter or keyword search`

This indicates that PeopleSoft did not register the typed parent class number.
Use the latest version of `monitor.py`, stop the current run, and restart it.
The current version enters the number through real key events before
submitting.

### Playwright timeout or `intercepts pointer events`

PeopleSoft may still be showing its processing overlay. The current monitor
detects when **Class Search and Enroll** is already selected instead of
clicking it again. Restart with the latest code. If the problem repeats,
Berkeley may be unusually slow; wait and retry.

### `CalCentral session expired`

Stop the process, sign back into the dedicated monitoring Chrome window,
restart CalCentral mode, and press Enter after Academics loads.

### Public page reports HTTP 403

The monitor automatically retries with `curl`. Confirm it is available:

```sh
curl --version
```

### `Drupal enrollment JSON was not found`

Berkeley may have changed the public page structure or the URL may not be an
individual section page. Open `COURSE_URL` in a browser and confirm it is the
specific section.

### Waitlist appears as `0/40` in PeopleSoft

The PeopleSoft component table pairs the current waitlisted count with the
section's enrollment capacity. The real maximum comes from the public
section's `maxWaitlist` field. The Discord message should therefore show a
value such as `Waitlist: 0/6`.

If the public lookup fails, check the configured URL template. As a temporary
fallback, set:

```dotenv
WAITLIST_CAPACITY=6
```

### A configuration correction creates a false change ping

The saved state still contains the previous interpretation. Stop the monitor
and remove only that section's generated CalCentral state file, then restart
to establish a new baseline. If you explicitly set `STATE_FILE`, make sure it
is unique to the monitored section.

### Webhook notification fails

Check that:

- `NOTIFICATION_WEBHOOK_URL` is complete and has no surrounding quotes or
  accidental spaces
- The Discord webhook still exists
- The machine has internet access
- The webhook channel permits posts

Run:

```sh
.venv/bin/python monitor.py --test-notification
```

### The monitor stopped

CalCentral continuous mode stops when:

- `Ctrl+C` is pressed
- Terminal is closed
- The monitoring Chrome window is closed
- The computer sleeps, restarts, or shuts down
- The Python process crashes

Keep Terminal, the monitoring Chrome window, the computer, and the internet
connection active.

## Adapting the monitor to a future course

### 1. Collect the identifiers

From Berkeley Class Search, collect:

- Academic term, such as `2027 Spring`
- Course label, such as `STAT 134 discussion 104`
- Component number, such as `104`
- Five-digit component class number, such as `20742`
- Five-digit parent lecture class number, such as `20746`
- Exact public section URL

Do not confuse these values:

- `DISCUSSION_NUMBER=104` is the displayed discussion number.
- `SECTION_ID=20742` is the discussion's five-digit PeopleSoft class number.
- `CALCENTRAL_PARENT_CLASS_NUMBER=20746` is the lecture searched to reveal its
  related discussions.

### 2. Configure `.env`

Example for a hypothetical STAT 134 Discussion 104:

```dotenv
COURSE_LABEL=STAT 134 discussion 104
DISCUSSION_NUMBER=104
SECTION_ID=20742
CALCENTRAL_PARENT_CLASS_NUMBER=20746
CALCENTRAL_TERM=2026 Fall

COURSE_URL=https://classes.berkeley.edu/content/2026-fall-stat-134-104-dis-104
CALCENTRAL_PUBLIC_SECTION_URL_TEMPLATE=https://classes.berkeley.edu/content/2026-fall-stat-134-{discussion_number}-dis-{discussion_number}

NOTIFICATION_WEBHOOK_URL=https://discord.com/api/webhooks/REPLACE_ME
DISCORD_USER_ID=123456789012345678
```

Keep the same Discord credentials if notifications should go to the same
channel and person. Replace them if the future monitor should use another
server, channel, or account.

### 3. Reset or separate state

Do not reuse another course's explicit `STATE_FILE`. Either remove the
variable and let CalCentral mode generate a class-specific filename, or set:

```dotenv
STATE_FILE=.calcentral_section_status_20742.json
```

### 4. Restart and verify

Reload `.env`, send a test, and run one real check:

```sh
set -a
. ./.env
set +a

.venv/bin/python monitor.py --test-notification
.venv/bin/python monitor.py --calcentral
```

Confirm that Terminal reports the intended discussion and that Discord shows
the correct course label, enrollment, and waitlist.

Then start continuous monitoring:

```sh
.venv/bin/python monitor.py --calcentral --continuous
```

### Component-type limitation

The current CalCentral automation specifically looks for a heading named
**Discussion Section**. Courses using **Laboratory Section**, studio, quiz, or
another component label require a small code change before reuse.

### Monitoring multiple sections

The program monitors one section per process. For multiple sections:

1. Create a separate `.env` file or launcher for each section.
2. Give each one a unique state file.
3. Start one process per section.
4. A separate browser-profile directory may be needed if the processes run
   simultaneously.

Never run two Playwright processes against the same
`.calcentral-browser-profile/` directory at the same time.

## Git and GitHub

The following paths are ignored and must remain private:

```text
.env
.venv/
.calcentral-browser-profile/
.section_status.json
.calcentral_section_status*.json
```

Create an empty GitHub repository without an initial README or `.gitignore`,
then initialize and inspect the local repository:

```sh
git init
git add .
git status
```

Confirm that `.env`, `.venv`, the CalCentral profile, and state files are not
staged. Commit and connect the remote:

```sh
git commit -m "Add Berkeley section monitor"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

For this project, the remote repository may be named
`Berkeley-Section-Monitor`, but the GitHub repository name has no effect on the
monitor configuration.

GitHub does not accept account passwords for Git pushes. On macOS, install and
authenticate GitHub CLI:

```sh
brew install gh
gh auth login --web --git-protocol https
gh auth setup-git
git push -u origin main
```

For later changes:

```sh
git status
git add monitor.py README.md requirements.txt test_monitor.py .gitignore
git commit -m "Describe the change"
git push
```

Do not use `git add -f` to force ignored secrets into the repository.

## Security notes

- Never commit `.env`.
- Never paste webhook URLs, SMTP passwords, CalNet credentials, cookies, or
  browser-profile files into GitHub issues or chat.
- Enter CalNet credentials only on Berkeley's genuine authentication page.
- Keep `.calcentral-browser-profile/` private.
- Revoke and replace a Discord webhook immediately if it is exposed.
- Use environment variables or deployment secrets for credentials.
- Public-mode deployment does not require CalNet credentials.
