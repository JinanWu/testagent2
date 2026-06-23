# Apple Reminders AppleScript fallback

Use this fallback when `remindctl` is unavailable or when you need a quick read-only listing from Reminders.app.

## List reminder list names

```bash
osascript -e 'tell application "Reminders" to get name of every list'
```

This returns a comma-separated list of reminder list names.

## List reminders in a specific list

```bash
osascript -e 'tell application "Reminders" to get name of every reminder of list "Personal"'
```

## Count reminders in every list

```bash
osascript <<'APPLESCRIPT'
tell application "Reminders"
set outText to ""
repeat with L in lists
  set outText to outText & (name of L) & "|" & (count of reminders of L) & linefeed
end repeat
return outText
end tell
APPLESCRIPT
```

## Notes

- Output is plain text, so for scripting split on commas and trim whitespace.
- The Reminders app must be available to AppleScript and the app may prompt for permission on first access.
- Prefer `remindctl --json` when you need structured output; use AppleScript as a fallback or for ad-hoc inspection.
