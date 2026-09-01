# Calendar query quirks observed on this macOS setup

Calendar.app event queries can be both semantically quirky and slow. The largest observed performance trap is loading full event collections in JXA and filtering afterward.

## Avoid full-history JXA scans

This pattern can time out on real calendars:

```javascript
var Calendar = Application('Calendar');
Calendar.calendars().forEach(function(cal) {
  cal.events().forEach(function(e) {
    var s = new Date(e.startDate());
    if (s >= start && s < end) ...
  });
});
```

Even though the code contains a date filter, `cal.events()` may materialize every historical event before JavaScript sees it.

## AppleScript object-specifier quirks

Broad object-specifier queries can also behave inconsistently:

- `every event of every calendar whose start date ≥ nowDate and start date < endDate` may return nested lists with empty sublists, not a flat list of event objects.
- Directly iterating that result and accessing `start date of e` can fail with coercion/object-specifier errors.

## Reliable bounded pattern

1. Prefer a known calendar when possible.
2. Query that calendar with Calendar.app's own bounded predicate.
3. Keep the time window small.
4. If the target calendar is unknown, loop calendar-by-calendar; do not use one broad `every calendar` object specifier.
5. Wrap each calendar query in `try` so one bad/subscribed calendar does not abort the run.

Example:

```applescript
set startDate to date "Monday, May 25, 2026 at 00:00:00"
set endDate to date "Tuesday, May 26, 2026 at 00:00:00"

tell application "Calendar"
  set out to ""
  repeat with cal in calendars
    try
      set evs to every event of cal whose start date ≥ startDate and start date < endDate
      if (count of evs) > 0 then
        repeat with e in evs
          set out to out & (name of cal) & tab & (summary of e) & tab & ((start date of e) as string) & tab & ((end date of e) as string) & linefeed
        end repeat
      end if
    end try
  end repeat
  return out
end tell
```

## Verification

After collecting or editing events, verify with the smallest possible scope:

- known calendar only
- exact title when known
- small start/end window
- for recurring events, first occurrence and optionally next occurrence only

Do not reintroduce a full-calendar JXA scan during verification.
