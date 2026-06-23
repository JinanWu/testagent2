---
name: apple-calendar
description: "Apple Calendar via Calendar.app and AppleScript/JXA for reading, searching, and updating events."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [Calendar, events, macOS, Apple, JXA, AppleScript]
prerequisites:
  commands: [osascript]
---

# Apple Calendar

Use this skill when the user wants to inspect, search, move, or edit events in Apple Calendar / Calendar.app.

## When to Use

- List calendars and events in Calendar.app
- Find events by date, title, or calendar name
- Move or resize events in Apple Calendar
- Inspect calendar data on macOS without using a third-party service

## Quick Approach

Use the fastest tool for the specific Calendar.app operation:

- **Creation and recurrence writes:** prefer AppleScript. Set recurrence with an RRULE string such as `FREQ=WEEKLY;INTERVAL=1`.
- **Bounded searches:** prefer Calendar.app's own AppleScript `whose start date ...` predicate on a known calendar and small date window.
- **JSON/property inspection:** use JXA when reading a small known set of events.

Avoid JXA full scans such as `cal.events().forEach(...)` across all calendars for date filtering; that can load full historical calendars and take 60+ seconds.

## Read / Search Patterns

### List calendar names
Use Calendar.app directly:
```bash
osascript -e 'tell application "Calendar" to get name of every calendar'
```

### Search events by a date range
Do **not** use JXA `cal.events().forEach(...)` over every calendar for date-range filtering. On real Calendar.app data this can materialize the full historical event set before JavaScript filters it.

Preferred approach:
1. If the calendar name is known, query only that calendar.
2. Use a bounded AppleScript query: `every event of cal whose start date ≥ startDate and start date < endDate`.
3. Keep the window small and wrap all-calendar discovery in `try`.

Example bounded query:
```applescript
set startDate to date "Monday, May 25, 2026 at 00:00:00"
set endDate to date "Tuesday, May 26, 2026 at 00:00:00"

tell application "Calendar"
  set cal to calendar "工作"
  set evs to every event of cal whose start date ≥ startDate and start date < endDate
  set out to ""
  repeat with e in evs
    set out to out & (summary of e) & tab & ((start date of e) as string) & tab & ((end date of e) as string) & linefeed
  end repeat
  return out
end tell
```

See `references/bounded-queries.md` for exact patterns and verification probes.

## Update Patterns

### Move an event by one day
When updating a timed event, set `endDate` before `startDate`.
That avoids the Calendar error that complains the start date must be earlier than the end date.

Recommended pattern:
1. Capture old start/end.
2. Compute new start/end.
3. Assign `endDate` first.
4. Assign `startDate` second.
5. Re-query the target date to verify.

Example:
```javascript
e.endDate = newEnd;
e.startDate = newStart;
```

### Create recurring meetings
For weekly recurring meetings, use the established AppleScript + RRULE pattern:
1. Confirm the target calendar (for work meetings, do not guess if multiple work calendars are plausible).
2. Check the exact first-occurrence window in that calendar to avoid duplicates.
3. Create the event normally.
4. Set `recurrence` to `FREQ=WEEKLY;INTERVAL=1`.
5. Verify with a bounded query against the target calendar and first occurrence window.

Do not internet-search or experiment with recurrence records during routine use; use `references/recurring-events.md`.

### Verification
After edits, always re-query, but keep verification bounded:
- target calendar only when known
- small date window around the target occurrence
- exact title + start time where possible
- for recurring events, verify the first occurrence and optionally the next occurrence; do not scan full history

## Pitfalls

- Complex AppleScript date-filter queries can fail with object-specifier errors, especially when attempted across every calendar at once. Prefer a known-calendar bounded query first; use all-calendar loops only with bounded predicates and `try`.
- Do **not** use JXA `cal.events().forEach(...)` across all calendars for date filtering. It can load complete historical calendars before filtering and cause 60+ second timeouts.
- On some Calendar.app datasets, `every event of every calendar whose ...` can produce nested lists / empty placeholders rather than a flat list of event objects. If that happens, loop calendar-by-calendar with the bounded predicate inside the loop.
- Time-zone and locale rendering in logs may differ from the actual stored event time; verify by querying the event again after update.
- Calendar edits are stateful; if a move fails halfway, re-read the event before trying again.
- Do not create scratch/test events in a user's real calendar unless explicitly approved. Prefer dry-run script generation, a temporary calendar, or a bounded duplicate check followed by direct creation.
- Recurring event deletion can be tricky: deleting an occurrence may create `excludedDates` while leaving the series master. Verify rollback carefully and avoid test writes.

## Support Files

- `references/apple-calendar-jxa-snippets.md` — JXA probes for small known event sets and JSON property inspection; not for full-history date scans.
- `references/calendar-query-quirks.md` — session-derived notes on reliable upcoming-event queries and a safe per-calendar loop pattern.
- `references/bounded-queries.md` — fast bounded query patterns that avoid full Calendar.app scans.
- `references/recurring-events.md` — concise recipe for creating and verifying weekly recurring events.

## Related Skills

- `apple-reminders` for personal to-dos and reminder lists.
