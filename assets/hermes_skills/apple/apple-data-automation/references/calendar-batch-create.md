# Calendar batch event creation recipe

Use when adding many Apple Calendar events from structured source data.

## Recommended pattern

- Open Calendar first if AppleScript reports the app is not running.
- Prefer one AppleScript batch over many one-off calls.
- Create or update events with explicit start/end datetimes.
- Build date objects by taking `current date` and setting `year`, `month`, `day`, `hours`, `minutes`, `seconds` instead of relying on locale-dependent `date "..."` parsing.
- For edits that touch both ends, set `end date` before `start date`.
- After writing, verify with a bounded re-query by summary and date window.

## Useful verification snippets

Count matching events in a calendar:

```applescript
tell application "Calendar"
  tell calendar "工作"
    count of (every event whose summary is "講座｜量化求職工作坊")
  end tell
end tell
```

Read back start/end for a summary:

```applescript
tell application "Calendar"
  tell calendar "工作"
    set evs to every event whose summary is "月會｜fable 寓意科技"
    repeat with e in evs
      log ((summary of e) & " | " & (start date of e as string) & " | " & (end date of e as string))
    end repeat
  end tell
end tell
```
