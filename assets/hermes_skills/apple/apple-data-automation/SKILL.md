---
name: apple-data-automation
description: Inspect and automate Apple Calendar, Freeform, and Health data workflows on macOS and iOS.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [Apple, Calendar, Freeform, Health, macOS, iOS, automation]
---

# Apple Data Automation

Use this skill when the user needs to inspect, automate, or summarize data from Apple apps or Apple Health sources, especially when the request spans multiple Apple personal-data surfaces.

## What this umbrella covers

- **Calendar.app / Apple Calendar**: read, search, move, resize, and create events safely.
- **Freeform**: inventory boards, inspect local metadata, and recover useful board structure.
- **Health / HealthKit**: export, summarize, and automate recurring health data workflows.

## Shared approach

1. Prefer the narrowest reliable source of truth.
   - Calendar: bounded app queries over full-history scans.
   - Freeform: Snapshot.plist first, database only if needed.
   - Health: compact exports or daily summaries rather than raw XML dumps.
2. Keep outputs compact and human-readable.
3. Verify with a small bounded re-read after any change or extraction.
4. Use app-specific tooling only after confirming the app/source and target scope.

## Calendar.app subsection

- Use AppleScript/JXA for reading, searching, and updating events.
- Prefer bounded queries on a known calendar and small date window.
- For frequent read-only agenda requests (today / tomorrow / this week / this month), the cached helper `python3 ~/.hermes/scripts/fast_calendar.py --range today --pretty` is fast, but it can miss recurring-event occurrences (observed: a weekly `retro` event appeared only as the prior master event through Calendar scripting/cache). For correctness-critical agenda reports, verify with EventKit (`EKEventStore.predicateForEvents`) or another occurrence-expanding query before finalizing.
- Keep the default calendar set unless the user explicitly asks for every calendar; use `--all-calendars` only when needed, but note this still may not expand recurring occurrences if using the cached helper.
- Avoid full-history scans across every calendar for agenda summaries; they are slow and unnecessary for day-level reporting.
- If JXA `whose` date filters return unexpectedly empty results or a broad scan is slow, retry with a bounded AppleScript query constructed from `current date` plus component setters.
- When creating or editing multiple events, batch them in one AppleScript run when practical, then verify with a bounded re-query.
- When Calendar reports the app is not running, open/activate Calendar and retry the AppleScript rather than assuming the calendar data is unavailable.
- When constructing event datetimes from structured data, prefer `current date` plus component setters (`year`, `month`, `day`, `hours`, `minutes`, `seconds`) instead of locale-dependent `date "..."` strings.
- When creating a single event reliably from JXA, prefer `app.make({new: 'event', at: calendar, withProperties: {...}})` over direct constructor-style attempts; verify by re-reading the created event.
- When moving or updating an event's time range, set `endDate` before `startDate`.
- For recurring events, verify the first occurrence after creation.
- Re-query the target date/window after any edit.

Related reference: `references/calendar-event-create-verify.md` for a known-good create/verify snippet.

### Agenda summary support

- See `references/today-tomorrow-agenda.md` for the combined Calendar + Reminders recipe used by day-level agenda questions.

## Freeform subsection

- Read `Snapshot.plist` first for board inventory.
- Deduplicate boards by UUID and filter tombstoned records.
- Inspect `boards.db` only when title inventory is not enough.
- Use cached previews and vision/OCR for dense boards.
- Treat Freeform as distinct from Apple Notes.

## Health subsection

- Prefer recurring compact summaries over raw Health export XML.
- Choose a stable output shape such as CSV, JSON, or one-line-per-day text.
- Capture only the fields the user actually needs.
- Verify a few sample days rather than the whole export.
- If multiple devices contribute data, confirm the source before interpreting trends.

## Pitfalls

- Do not confuse Freeform with Apple Notes.
- Do not do full-history scans when the user only asked for a small date range or inventory.
- For calendar-based work planning, query only the target day/week first. Month-level inventory is acceptable for orientation, but avoid full event iteration across all calendars unless the user explicitly needs it.
- If a JXA `whose` date-filter query unexpectedly returns an empty result, retry with a bounded AppleScript query using component-built dates before concluding there are no events.
- Do not build a broad Health schema when a narrow summary is sufficient.
- Do not create test events in a real calendar unless explicitly approved.

## Verification habits

- Calendar: re-query the target date/window after edits.
- Freeform: compare unique UUIDs and activity times.
- Health: check coverage and a few sample rows/days.

## Related support files

- `references/calendar-quick-notes.md` — short Calendar workflow reminders.
- `references/calendar-batch-create.md` — batch event creation / verification recipe for structured imports.

This umbrella intentionally keeps the class-level instructions here; session-specific recipes belong in per-topic references under the relevant leaf skills or in future support files.
