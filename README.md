# Berkeley discussion-section monitor

This is a one-shot monitor for [2026 Fall MATH 113 discussion
104](https://classes.berkeley.edu/content/2026-fall-math-113-104-dis-104).
Run it every five minutes with a scheduler; it does not stay alive between
checks.

## Website inspection

Inspected July 29, 2026:

- The initial, unauthenticated HTML contains the section data. Login and
  cookies are not required.
- There is no need to call a separate JSON endpoint. Drupal embeds structured
  JSON in the `script[data-drupal-selector="drupal-settings-json"]` element at
  `ucb.enrollment.available`.
- The embedded enrollment record ID is `27743`. The displayed discussion/class
  number is `104`.
- The authoritative availability field is
  `enrollmentStatus.status`: code `O` means open and code `C` means closed.
  Seat fields provide useful context and a fallback:
  `enrolledCount`, `maxEnroll`, `waitlistedCount`, `maxWaitlist`, and
  `openReserved`.
- At inspection time the section was `Closed` (`C`), with 40 of 40 enrolled,
  0 of 6 waitlist places used, and 0 open reserved seats.

The monitor therefore uses `requests` plus Beautiful Soup to read the embedded
JSON. Berkeley currently rejects Requests' TLS fingerprint with HTTP 403 on
some CDN edges, so the fetcher transparently retries with the system `curl`
command. This is still a lightweight direct HTTP request; Playwright would add
complexity without improving reliability.

## Local setup

Requires Python 3.10 or newer.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`, then export it before running:

```sh
set -a
. ./.env
set +a
python monitor.py
```

To keep it running in the current terminal until you press `Ctrl+C`:

```sh
python monitor.py --continuous
```

Test the configured notification without changing the saved section state:

```sh
python monitor.py --test-notification
```

It checks every 300 seconds (five minutes) by default. To choose another
interval of at least 30 seconds:

```sh
python monitor.py --continuous --interval 120
```

Closing the terminal also stops the process. For monitoring that should
continue after logout or reboot, use the scheduler approach below or a service
manager instead.

## Experimental live CalCentral mode

CalCentral mode uses a dedicated local Chrome profile instead of putting a
CalNet password or session cookie in `.env`. Start it with:

```sh
.venv/bin/pip install -r requirements.txt
.venv/bin/python monitor.py --calcentral --continuous
```

Chrome opens to CalCentral. Sign in and complete Duo yourself, navigate to MATH
113's class search automatically by parent lecture class number `22491`, and
reads the `104 #27743` row from the Discussion Section table. You only need to
wait for the CalCentral academics page to load and press Enter in Terminal.
The monitor never selects the row or clicks Enroll/Add to Cart. It repeats the
read-only search every five minutes. Press `Ctrl+C` to stop it.

Choose a different discussion by setting its displayed discussion number and
five-digit PeopleSoft class number in `.env`:

```dotenv
DISCUSSION_NUMBER=106
SECTION_ID=27745
CALCENTRAL_PARENT_CLASS_NUMBER=22491
```

`DISCUSSION_NUMBER` also changes the Discord notification title. If monitoring
a different course entirely, set `COURSE_LABEL` and its public `COURSE_URL` as
well. Other defaults can be overridden with `CALCENTRAL_TERM`. CalCentral mode
uses a separate state file for each five-digit class number, so changing
sections or switching from the cached public-page data does not trigger a
false change alert.

The login is remembered only in `.calcentral-browser-profile/` on this
computer. Do not upload or share that directory. CalCentral mode is intended
for a local, continuously running terminal; PeopleSoft sessions expire, so it
is less suitable for cron or GitHub Actions than the public-page mode.

The first successful run establishes the baseline and deliberately sends
nothing, even if the section is already open. Later runs notify only when a
previously closed section becomes open. State is stored atomically in
`.section_status.json`. If notification fails, the old state remains so the
next run retries.

Configure either `NOTIFICATION_WEBHOOK_URL` (Slack/Discord-compatible) or the
SMTP variables in `.env`. With a Discord webhook, set `DISCORD_USER_ID` to your
numeric Discord user ID to ping that account. Secrets, tokens, passwords, and
login cookies are not stored in source code.

When a webhook is configured, every successful check posts the current status
and its Berkeley/Pacific check time. The configured Discord user is mentioned
only when the status, enrolled count, capacity, waitlist count, waitlist
capacity, or open reserved-seat count changes. Routine unchanged checks do not
ping the user.

## Schedule every five minutes

With the virtual environment created, run `crontab -e` and add one line,
replacing both absolute paths:

```cron
*/5 * * * * cd "/absolute/path/to/project" && set -a && . ./.env && set +a && "/absolute/path/to/project/.venv/bin/python" monitor.py >> monitor.log 2>&1
```

Each invocation performs one check and exits. The log records its timestamp,
status, enrollment, waitlist, and whether the section is open. A network
failure, malformed response, missing section, corrupt state file, or changed
page structure produces a clear error and nonzero exit instead of a crash.

For PythonAnywhere, create a scheduled task using the same one-shot command.
For GitHub Actions, use a `*/5 * * * *` workflow schedule, store notification
credentials as repository secrets, and persist `.section_status.json` in
durable storage (for example, a dedicated state branch or external key/value
store); ordinary runner files disappear after each job.

## Tests

```sh
python -m pytest -q
```
