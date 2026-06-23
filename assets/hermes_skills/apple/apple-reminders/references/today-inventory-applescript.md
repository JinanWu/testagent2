# App-wide today inventory via AppleScript

Use this recipe when the user asks for today’s reminders across all lists.

## Goal
Return a compact list of unfinished reminders whose due date falls within the local today window, including list name, title, due time, and priority when available.

## Reliable pattern

1. Build local day bounds from `current date`.
2. Iterate `repeat with listObj in every list`.
3. Re-resolve the list by name inside the loop (`list listName`) instead of reusing list references across iterations.
4. Query unfinished reminders, then inspect each reminder’s `due date` inside a `try` block so reminders with no due date are skipped cleanly.
5. Keep output lightweight: `list\ttitle\tdue\tpriority`.

## Known pitfall

- Do not loop over `name of every list` directly as the iterator object; that can coerce into text-item behavior instead of list objects.
- Do not assume every unfinished reminder has a due date; guard `due date` reads individually.

## Example shape

```text
提醒事項	看cablate的課程	2026年6月6日 星期六 晚上8:00:00	9
iot建立	確認專案需求與第一版範圍	2026年6月6日 星期六 上午10:30:00	0
```

## Use with

- Day-level agenda summaries
- Fast triage of all today-due reminders
- Compact planning output where long bodies/notes are unnecessary
