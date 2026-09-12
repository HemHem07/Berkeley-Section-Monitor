# Berkeley enrollment automation: feasibility review

Research date: September 11, 2026. Research only; no enrollment automation was
implemented and no enrollment transactions were attempted.

## Assessment

A Chrome extension that assists with Berkeley enrollment appears technically
plausible. An unattended tool that reliably enrolls whenever a seat appears is
a larger, less certain project. My recommendation is to retain the desktop
monitor and, if pursued, prototype an attended enrollment helper before making
an unattended enrollment feature a dependency of this app.

## The UC Davis extension

The likely reference is **Aggie Schedule Sniper**. Its developer describes a
countdown to the student's registration pass time and an automatic click on
**Register ALL** in Schedule Builder. Its setup instructions require an open
tab, an awake computer, and an internet connection. This is evidence for a
scheduled browser action, not proof of continuous seat-opening enrollment or
a supported university registration API. I reviewed the developer's README
and store description; retrieval of its architecture/source files failed, so
this is not a source-code audit.

Sources: [Developer README](https://github.com/vijitdua/aggie-schedule-sniper/blob/releases/README.md),
[Chrome Web Store listing](https://chromewebstore.google.com/detail/aggie-schedule-sniper/pielpncpeneeldacmfnbmbdkapgbhibo).

## What Berkeley exposes

Berkeley has a real API management platform and lists an Enrollment API in its
platform documentation. API Central allows CalNet users to request credentials;
restricted access is routed to the data owner. Application credentials are
distinct from a student's normal CalCentral login. The existence of an
Enrollment API does not establish that it accepts student enrollment writes.

Sources: [API Central access workflow](https://integration-services.berkeley.edu/node/336),
[Authentication and authorization](https://integration-services.berkeley.edu/api-management/authentication-authorization),
[API platform documentation mentioning Enrollment API](https://integration-services.berkeley.edu/node/357).

I did **not** verify a documented, student-accessible endpoint for adding,
dropping, or changing sections. The current developer portal linked from the
gateway is [developers.api.berkeley.edu](https://developers.api.berkeley.edu/);
it returned HTTP 403 to the research tool. The next API-specific investigation
would be an authorized review of that catalog and its endpoint permissions.
This is an access/documentation gap, not proof that a write API does not exist.

The bCourses [Canvas Enrollments API](https://bcourses.berkeley.edu/doc/api/enrollments.html)
manages LMS course membership. It is not evidence of an API that secures a seat
in Berkeley's official registration system.

## Approaches worth considering

| Approach | Assessment |
| --- | --- |
| Official enrollment-write API | Best integration if documented and access is granted; neither was established here. |
| Chrome extension assisting the signed-in enrollment page | Most plausible prototype. Uses the student's existing Chrome session and can surface the chosen class and submission result. |
| Replaying undocumented requests | Poor starting point. Would depend on unverified authentication and transaction state and require substantial reverse engineering. |
| Extend the current Playwright monitor to submit | Technically plausible, but changes a read-only monitor into a transaction tool and carries the same enrollment validation burden. |

An extension can read and modify matched pages through content scripts. A
proposed Berkeley extension would restrict its page access to the relevant
CalCentral/PeopleSoft pages, with frame matching where necessary. Its settings
would contain class choices, not exported cookies or CalNet credentials.
Chrome's extension background workers can be terminated when idle, so a
background `setInterval` is not a reliable scheduler. Persisted state and
Chrome's supported scheduling mechanisms would be needed.

Sources: [Chrome content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts),
[Extension worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle).

## Why an open-seat alert is not enough

Berkeley documents reserved-seat eligibility, time-conflict restrictions,
and automatic versus manual waitlists. Automatic waitlists already provide
unattended enrollment when their conditions are met. A visible seat therefore
does not establish that this particular student can enroll immediately.
These are reasons to validate the actual transaction result, not just count
an attempted click as success.

Source: [Registrar enrollment guidance](https://registrar.berkeley.edu/enrollment/how-to-enroll-in-classes/).

The current monitor already handles a multi-step PeopleSoft navigation flow,
session expiration, and CalNet/Duo sign-in. That local implementation suggests
the read-side building blocks are reusable; it does not validate enrollment
submission. No conclusion about Berkeley's permission for unattended enrollment
automation was established by this review.

## Proposed first prototype, if we pursue it

1. Read the selected term, course, section, and current enrollment status from
   the student's signed-in page. Validate these against the intended target.
2. Help navigate to the desired class and display the final choices for review,
   including grading basis, units, and any waitlist option.
3. Let the student explicitly submit. Read and display the confirmed outcome
   or enrollment error; never silently drop or swap an existing class.
4. Test against saved/synthetic page fixtures first. Assess reliability with
   attended real use before considering a narrowly scoped opt-in automatic mode.

I would pursue that helper if navigating from alerts is a frequent pain point.
I would defer unattended enrollment until the interface, permissions, and
success/failure handling are established. The Davis project is a useful
reference for the user experience, not a drop-in Berkeley enrollment engine.
