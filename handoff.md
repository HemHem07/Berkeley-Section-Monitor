# Berkeley Section Monitor — handoff

Updated: 2026-09-12. This document summarizes the implemented app and conversation context. Inspect the current code and Git diff before continuing; this is not a substitute for verification.

## Project and purpose

Windows desktop Python app that monitors UC Berkeley lecture/discussion availability through public Berkeley class pages or a signed-in CalCentral/PeopleSoft browser. Tracks multiple sections independently and sends Discord/webhook or email alerts. It does not enroll students or modify enrollment.

Workspace: `C:\Users\hemda\Documents\Code Stuff\Personal\Berkeley-Section-Monitor`.
Use PowerShell and `.venv\Scripts\python.exe`. Launch with `Start Monitor.bat` or VS Code's configured Python run/debug entry. Dependencies are in `requirements.txt`.

## Architecture

- `desktop.py`: pywebview window, Python/JavaScript API, pystray icon, single-instance Windows lock, startup and shutdown. HTML/CSS/JS are embedded locally by `dashboard_html()`.
- `desktop_backend.py`: saved watchlist, settings, ordered classes, bounded activity history, per-class worker subprocesses, lifecycle and event handling.
- `desktop_worker.py`: wraps monitor logic, emits structured JSON events, receives stop/check-now/sign-in-focus commands via stdin. Supports continuous and one-shot runs.
- `monitor.py`: public data parsing, CalCentral browser flow, availability normalization, saved baselines, notification rules and delivery.
- `courses.py`: shared public course discovery, validated profile storage, stable URL hashing, and environment conversion.
- `setup_ui.py`: older Tk setup interface using the shared course functions.
- `lecture_context.py`: discovers actual associated lectures through Berkeley's published association endpoint and fetches their public counts. Validates term/course/class identity rather than guessing section numbering.
- `discord_settings.py`: validates Discord settings, atomically updates two dotenv keys while retaining unrelated settings, and sends explicitly requested test messages.
- `desktop_ui/index.html`, `styles.css`, `app.js`: dashboard, forms, themes, countdown, grouping, ordering and interaction behavior.
- `tests/`: unit/integration tests and opt-in browser/native/live tests.

## Current product behavior

### Monitoring and window lifecycle

- Classes start paused on a fresh launch. Start/Pause operate independently; Start all/Pause all are available.
- Closing hides to tray if the tray is available. Quit stops workers and exits. Without a working tray, native close exits instead of hiding an inaccessible app.
- Check now runs one check on a paused class and returns it to paused; on a waiting active class it requests an immediate check.
- Dashboard polling is every 1 second, with refresh on focus/visibility return. Worker scheduling is independent of UI refresh.
- Sleep/hibernate/shutdown prevent normal monitoring. Screen off, lock, and tray hiding allow monitoring while the machine stays awake and connected. Sleep/wake recovery has not been established by a long real-world test.
- Window identity is “Berkeley Monitor,” with a generated gold-column/navy icon for the shell and tray. Windows theme attributes request hidden caption text/icon while retaining native controls and resizing. Windows AppUserModelID is set. Native tests checked identity/icon and lifecycle, but do not visually prove appearance on every Windows version.

### Cards, courses and sources

- Use the exact discussion URL to monitor a discussion, not its lecture's URL. CalCentral discussions also require a parent lecture class number.
- LIVE means CalCentral; PUBLIC means Berkeley public data. The explicit section status is authoritative: Closed can coexist with unused numeric waitlist capacity.
- Two or more tracked discussions with matching course/term URL prefixes group automatically. Only sections explicitly added are monitored.
- Groups share lecture display per associated lecture ID; workers still fetch lecture context independently. Different parent lectures remain separate. Lecture data always comes from public pages, even for LIVE discussion cards.
- Group actions: Start course, Pause course, Add discussion. Add discussion copies source/interval/parent/alert preference into a blank-URL form; users should verify the appropriate parent for their new section.
- Each section retains its own alert preference, counts, timer and controls. Grouping is not a combined notification pipeline: qualifying changes alert per section.
- Standalone cards move past entire groups in one step. Course headers move the whole group; discussion menu moves only reorder within the group. Order persists in the flat saved class list, and edits preserve position.
- Backend `course_group_key` supplies a derived `group_key` in snapshots and drives reordering. The frontend groups by that key once per render. Winter discussions remain standalone, preserving previous behavior.
- Lecture lookup failures leave monitoring intact and retain last-known lecture context. Before successful lookup a placeholder is shown.

### Freshness

- `staleReading` marks section data stale after `max(120 seconds, 2 × interval)` since the last successful check.
- A stale section gets an amber notice, muted availability styling, and last-known caption. It is excluded from the group's reported-open count. Successful checks clear the warning; failures do not update success time.
- Paused old readings can also become stale. No baseline remains “Awaiting first check,” not stale.
- Current automatic age-based stale warning is for section cards. Parent lecture panels have their own checked timestamp/error/paused wording but do not yet use that same age-based warning.

### Notifications

- Seat-opening, waitlist-opening, any-change and muted policies, plus a saved default. First reading establishes a baseline; desktop mode suppresses routine unchanged/startup messages.
- Optional Discord user ID enables mentions. Sign-in alerts remain enabled even for enrollment-muted classes. A post without a mention is not necessarily silent under Discord's own notification settings.
- Legacy terminal mode has different behavior and can send routine non-mention updates.
- Preferences contains masked webhook entry, optional user ID, Save Discord settings and Send test notification. Blank webhook preserves the current secret; blank user ID removes mentions. Stored webhook is never returned to the UI.
- Saving updates `.env` and the app environment, restarting active workers while leaving paused classes paused. Test uses saved settings, refuses unsaved edits in the UI, and posts only when clicked.
- “Configured” only means credentials are present. Cards/activity report actual enrollment delivery results. Test success means Discord accepted the request.
- Webhook takes precedence over email. Email setup remains in `.env`.

### UI decisions and latest change

- Saved Light/Dark/System appearance; charcoal dark surfaces and muted gold accents. Rounded scrollbars throughout HTML UI.
- Preferences, Hide to tray and Quit have icons. Removed redundant standalone Discord-settings button; Preferences owns setup, cards show alert policy, and a warning appears if no delivery channel is configured.
- Card menus dismiss on outside click/Escape and opening another menu. Escape returns focus to the trigger.
- Recent activity expands/collapses with animation and honors reduced-motion settings; latest 200 structured events are retained.
- Clicking outside Preferences saves theme/default alert policy and closes. Discord credentials still require their own Save button. Cancel discards pending general edits. Saving an unchanged default does not restart workers.
- **Most recent change:** clicking outside the class Add/Edit dialog submits through `requestSubmit()`, so browser validation and the normal save/error path are retained. Invalid fields or backend errors keep it open. Cancel discards edits. A disabled-save guard prevents duplicate submissions. Both pointerdown and click must be outside to avoid accidental dismissal from a drag starting inside.
- Class-search link has spacing below the URL field and explicit light/dark hover/focus styling.

## Refactoring status on 2026-09-12

Implementation paused at final verification as requested. The combined run had 149 passed, 2 live checks skipped, and 1 Tk test failure: its moved save mock targeted `courses.save_profile` instead of the picker's imported `setup_ui.save_profile`. The accidentally inserted fixture profile was removed while retaining the original saved entry; the mock target was corrected. That correction still needs verification before calling the refactor complete.

- Public and CalCentral readings share `process_status`; failed delivery retains the prior notification baseline.
- Worker errors use explicit notification classification and sanitized events. Cancellation uses a checkpoint hook, not replacement of the `time` module.
- Dashboard settings are committed in memory after atomic replacement succeeds. Saved class/status/activity records are validated before startup rewrites; unknown fields and version 1 are retained.
- Failed worker startup cleans up its child. Malformed events are handled without ending the reader, and stopping attempts every worker with bounded waits. Failed termination retains the worker for another Pause attempt.
- Removed the unreachable guided-detail parser and temporary batch fragment. The active discussion-row and lecture-popup paths remain.
- Browser QA uses independent fixture-backed tests for cards, grouping, preferences, dialogs, activity, and polling focus. No dependencies were added.

## Verification

Default suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Rendered HTML UI checks (Chrome/Playwright):

```powershell
$env:RUN_DESKTOP_UI_TESTS='1'
.\.venv\Scripts\python.exe -m pytest tests/test_desktop_ui.py -q
```

Native Windows WebView2/tray check:

```powershell
$env:RUN_DESKTOP_NATIVE_TESTS='1'
.\.venv\Scripts\python.exe -m pytest tests/test_desktop_native.py -q
```

Optional read-only Berkeley public checks: set `RUN_LIVE_PUBLIC_TESTS=1`. They previously verified MATH 113 discussions 104/105 resolve to lecture 001, class 22491. Do not assume historical enrollment counts remain current.

Native tests may need tool escalation because sandboxed WebView2 initialization can fail. Tests use isolated dashboard state; notification tests mock delivery. Do not send real Discord messages merely to verify an unrelated change.

Recent evidence (separate runs, not a claim about a fresh full-suite run):

- Course grouping: full run with UI enabled, 97 passed / 8 skipped.
- Reordering fix: desktop/backend plus UI tests, 26 passed.
- Stale-data changes: rendered UI test passed.
- Most recent class-dialog outside-click change: rendered UI test passed, 1 passed in 9.82s.
- Native title/icon/tray test passed when implemented.

Rendered QA screenshots are under ignored `.desktop-qa/`. Check current skip reasons before claiming comprehensive coverage; real authenticated CalCentral flows are not routinely exercised.

## Local data and Git

Do not expose or overwrite `.env`, `.dashboard.json`, `.monitor-profiles.json`, `.monitor-state/`, or `.calcentral-browser-profile/`. They hold secrets, user settings, baselines or authenticated sessions and are ignored by Git. `.desktop-qa/`, locks, caches and temporary Discord credential files are also ignored.

At handoff creation, `git status --short` showed only `desktop_ui/app.js` and `tests/test_desktop_ui.py` modified before this document was added. Those are the latest class-dialog changes. The user previously said they pushed the project, but remote synchronization was not verified in this task.

The user is learning Git. Explain save/stage/commit/push plainly if asked. Do not commit or push without a request. Preserve existing work.

## Collaboration context and possible follow-up

User likes concise updates, practical UI polish, natural dark mode and minimal duplicate controls. They prefer continuing authorized work without repeated confirmation. Do not restart their actual running monitor automatically; usually ask them to Quit/reopen after desktop changes.

No new feature beyond this handoff is currently requested. Suggested future work was reliability testing through long sessions, tray use, sleep/wake and CalCentral session expiry. Auto-enrollment research exists in `docs/enrollment-automation-research.md`; no auto-enrollment was implemented or newly authorized.
