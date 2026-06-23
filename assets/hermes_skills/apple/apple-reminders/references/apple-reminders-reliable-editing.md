# Apple Reminders reliable editing recipe

When `remindctl` is unavailable, use AppleScript with a two-step pattern that proved reliable for this session:

## Create first, then edit fields

1. Create the reminder with only a title.
2. Set `body` separately.
3. Set `due date` separately using `current date` and adjust year/month/day/hours/minutes.
4. Verify with `properties of` or by reading back the specific fields.

## Example

```bash
osascript <<'APPLESCRIPT'
tell application "Reminders"
  set targetList to list "提醒事項"
  set r to make new reminder at end of reminders of targetList with properties {name:"Example title"}
  set body of r to "Example body"
  set d to current date
  set year of d to 2026
  set month of d to May
  set day of d to 21
  set hours of d to 17
  set minutes of d to 59
  set seconds of d to 0
  set due date of r to d
  return properties of r
end tell
APPLESCRIPT
```

## Verification

Prefer checking:
- `name of r`
- `body of r`
- `due date of r as text`
- `properties of r`

If one-shot creation appears to succeed but fields are missing, split the operation into separate AppleScript calls.
