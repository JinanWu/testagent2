# Apple Reminders session notes

This note captures a reliable workflow for creating and verifying reminders via AppleScript when `remindctl` is unavailable.

## Known-good creation flow

1. Create the reminder with just the title.
2. Verify it exists by listing names or counting reminders in the target list.
3. Set `body` separately.
4. Set `due date` separately.
5. Read back `properties` to confirm both fields landed.

## Example

```bash
osascript <<'APPLESCRIPT'
tell application "Reminders"
  set targetList to list "提醒事項"
  set r to make new reminder at end of reminders of targetList with properties {name:"ai文章撰寫與發送"}
  set body of r to "為了今天發送ai文章贏得五月的文章最高贊數的獎勵而進行，主題是要介紹ai相關的使用方式"
  set d to current date
  set hours of d to 18
  set minutes of d to 0
  set seconds of d to 0
  set due date of r to d
  return properties of r
end tell
APPLESCRIPT
```

## Verification helpers

```bash
osascript -e 'tell application "Reminders" to count reminders of list "提醒事項"'
osascript -e 'tell application "Reminders" to get name of reminders of list "提醒事項"'
```

## Notes

- Prefer separate property assignment for `body` and `due date` when you need a reliable write.
- Use `properties of r` to confirm `body`, `due date`, and `remind me date` are populated.
- If the script output is empty, verify by reading back the reminder rather than assuming the write failed.
