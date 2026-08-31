# Apple Calendar recurring event recipe

Reliable pattern for creating weekly recurring events in Calendar.app without slow scans or repeated internet/tool exploration.

## Key decisions

- Use AppleScript for creation and setting recurrence.
- Use an RRULE string for recurrence: `FREQ=WEEKLY;INTERVAL=1`.
- Avoid structured recurrence records such as `{frequency:weekly, interval:1}`; they are unreliable in Calendar.app scripting.
- Verify with a bounded query against the target calendar and small date window.
- Do not create scratch/test events in a real user calendar unless explicitly approved. Prefer a temporary calendar or generate the script as dry-run text first.

## Minimal creation pattern

```applescript
tell application "Calendar"
  tell calendar "工作"
    set s to current date
    set hours of s to 10
    set minutes of s to 0
    set seconds of s to 0
    set e to s + (1 * hours)
    set ev to make new event at end of events with properties {summary:"sprint planning", start date:s, end date:e}
    set recurrence of ev to "FREQ=WEEKLY;INTERVAL=1"
  end tell
end tell
```

## Example: create three weekly meetings from a known Monday

```applescript
-- Assumes current date is the Monday you want as the first sprint planning occurrence.
tell application "Calendar"
  tell calendar "工作"
    set mondayStart to current date
    set hours of mondayStart to 10
    set minutes of mondayStart to 0
    set seconds of mondayStart to 0
    set mondayEnd to mondayStart + (1 * hours)
    set ev1 to make new event at end of events with properties {summary:"sprint planning", start date:mondayStart, end date:mondayEnd}
    set recurrence of ev1 to "FREQ=WEEKLY;INTERVAL=1"

    set thursdayStart to mondayStart + (3 * days)
    set hours of thursdayStart to 15
    set minutes of thursdayStart to 0
    set seconds of thursdayStart to 0
    set thursdayEnd to thursdayStart + (2 * hours)
    set ev2 to make new event at end of events with properties {summary:"RD組會議", start date:thursdayStart, end date:thursdayEnd}
    set recurrence of ev2 to "FREQ=WEEKLY;INTERVAL=1"

    set fridayStart to mondayStart + (4 * days)
    set hours of fridayStart to 16
    set minutes of fridayStart to 0
    set seconds of fridayStart to 0
    set fridayEnd to fridayStart + (2 * hours)
    set ev3 to make new event at end of events with properties {summary:"retro", start date:fridayStart, end date:fridayEnd}
    set recurrence of ev3 to "FREQ=WEEKLY;INTERVAL=1"
  end tell
end tell
```

## Avoid duplicates before creating

Before creating, check the target calendar and exact first-occurrence window. Do not scan every event in every calendar.

```applescript
set checkStart to date "Monday, May 25, 2026 at 09:30:00"
set checkEnd to date "Monday, May 25, 2026 at 11:30:00"

tell application "Calendar"
  set cal to calendar "工作"
  set matches to every event of cal whose start date ≥ checkStart and start date < checkEnd and summary is "sprint planning"
  if (count of matches) is 0 then
    -- safe to create
  end if
end tell
```

## Fast verification

Verify only the target calendar and the first occurrence window:

```applescript
set checkStart to date "Monday, May 25, 2026 at 09:30:00"
set checkEnd to date "Monday, May 25, 2026 at 11:30:00"

tell application "Calendar"
  set cal to calendar "工作"
  set matches to every event of cal whose start date ≥ checkStart and start date < checkEnd and summary is "sprint planning"
  set out to ""
  repeat with e in matches
    set out to out & (summary of e) & tab & ((start date of e) as string) & tab & ((end date of e) as string) & tab & (recurrence of e) & linefeed
  end repeat
  return out
end tell
```

## .ics import fallback

If Calendar.app scripting cannot set recurrence reliably, create a small `.ics` file and import it. This is safer than internet searching during the task.

Minimal weekly event:

```ics
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Hermes Agent//Calendar Automation//EN
BEGIN:VEVENT
UID:sprint-planning-20260525T100000@hermes.local
DTSTAMP:20260525T010000Z
DTSTART;TZID=Asia/Taipei:20260525T100000
DTEND;TZID=Asia/Taipei:20260525T110000
RRULE:FREQ=WEEKLY;INTERVAL=1
SUMMARY:sprint planning
END:VEVENT
END:VCALENDAR
```

Import manually or via `open /path/to/file.ics`. If using this fallback, tell the user where the file is and verify import afterward.

## Deletion/rollback caveat

Recurring event deletion through Calendar.app scripting can behave differently from single events: deleting an occurrence may create `excludedDates` while leaving the recurring master event. Do not assume a `delete` call fully removed the series. For rollback:

1. Prefer avoiding test writes in the first place.
2. If rollback is needed, query by target calendar + UID/title + first occurrence window.
3. Ask the user before destructive deletion of a recurring series if ambiguity remains.
4. Verify that the event no longer appears in the first occurrence and next occurrence windows.

## Pitfalls

- `make new recurrence rule with properties ...` is not a reliable Calendar.app pattern.
- `set recurrence of ev to {frequency:"weekly", interval:1}` fails because Calendar expects text.
- `set recurrence of ev to "FREQ=WEEKLY;INTERVAL=1"` is the known-good pattern.
- Avoid `cal.events().forEach(...)` verification; it can load full history and time out.
