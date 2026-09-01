# Apple Calendar bounded query patterns

Use these patterns to avoid accidentally loading the full Calendar.app history.

## Core rule

Do **not** use this pattern for date search on real Calendar.app data:

```javascript
Calendar.calendars().forEach(function(cal) {
  cal.events().forEach(function(e) {
    var s = new Date(e.startDate());
    if (s >= start && s < end) ...
  });
});
```

`cal.events()` can materialize every historical event in the calendar before JavaScript filters it. On large calendars or subscribed calendars this can take 60+ seconds or time out.

## Preferred strategy

1. If the target calendar is known, query only that calendar.
2. Use Calendar.app's own bounded `whose start date ...` predicate where possible.
3. Keep the time window small: one day, one week, or the exact meeting start/end window.
4. For all-calendar discovery, loop calendar-by-calendar, but each query must still be bounded and wrapped in `try`.
5. Verification after edits should query only the target calendar and small window or exact title/start time.

## Fast AppleScript bounded query for one calendar

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

## Safer all-calendar bounded discovery

Use this only when the target calendar is unknown:

```applescript
set startDate to date "Monday, May 25, 2026 at 00:00:00"
set endDate to date "Tuesday, May 26, 2026 at 00:00:00"

tell application "Calendar"
  set out to ""
  repeat with cal in calendars
    try
      set evs to every event of cal whose start date ≥ startDate and start date < endDate
      repeat with e in evs
        set out to out & (name of cal) & tab & (summary of e) & tab & ((start date of e) as string) & tab & ((end date of e) as string) & linefeed
      end repeat
    end try
  end repeat
  return out
end tell
```

## Exact verification after creating an event

After creating a meeting, verify by target calendar + small date window + title/start time:

```applescript
set startDate to date "Monday, May 25, 2026 at 09:30:00"
set endDate to date "Monday, May 25, 2026 at 11:30:00"

tell application "Calendar"
  set cal to calendar "工作"
  set evs to every event of cal whose start date ≥ startDate and start date < endDate and summary is "sprint planning"
  return (count of evs) as string
end tell
```

## JXA role

Use JXA for compact JSON output after a bounded AppleScript query has identified a small set, or for reading known event properties. Avoid JXA full scans over `cal.events()` for date filtering.
