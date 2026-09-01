# Apple Calendar JXA Snippets

This file collects JXA patterns for Calendar.app automation on macOS.

## Important performance warning

Avoid using JXA to date-filter by scanning full event collections:

```javascript
// Avoid on real calendars: may load full history before filtering.
Calendar.calendars().forEach(function(cal) {
  cal.events().forEach(function(e) { ... });
});
```

For date-range queries, use the bounded AppleScript patterns in `references/bounded-queries.md` instead.

## List calendars

```bash
osascript -e 'tell application "Calendar" to get name of every calendar'
```

## Read known event properties as JSON

Use this only when the calendar and title/time window are already narrowed enough that the event set is small.

```javascript
var Calendar = Application('Calendar');
var cal = Calendar.calendars.byName('工作');
var out = [];
cal.events().forEach(function(e) {
  // OK only for a small target calendar / narrow task; avoid for broad date search.
  if (e.summary() === 'sprint planning') {
    var p = e.properties();
    out.push({
      uid: p.uid,
      summary: p.summary,
      start: p.startDate,
      end: p.endDate,
      recurrence: p.recurrence,
      excludedDates: p.excludedDates
    });
  }
});
console.log(JSON.stringify(out, null, 2));
```

## Move an event one day forward

Important: set `endDate` before `startDate`.

```javascript
var Calendar = Application('Calendar');
var oldStart = new Date(e.startDate());
var oldEnd = new Date(e.endDate());
var newStart = new Date(oldStart.getTime() + 24*60*60*1000);
var newEnd = new Date(oldEnd.getTime() + 24*60*60*1000);
e.endDate = newEnd;
e.startDate = newStart;
```

## Verification probe

After editing, query the destination day again using a bounded AppleScript query or an exact small-set JXA lookup. Do not verify by scanning all events in all calendars.
