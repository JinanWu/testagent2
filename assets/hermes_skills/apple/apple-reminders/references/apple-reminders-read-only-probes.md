# Apple Reminders read-only probes

Use these when you need to inspect reminders without editing anything.

## List reminders in a specific list

```bash
osascript -e 'tell application "Reminders" to get name of every reminder of list "提醒事項"'
```

## Read reminder completion state

```bash
osascript -e 'tell application "Reminders" to get {name, completed} of every reminder of list "提醒事項"'
```

## Notes

- Quote the list name exactly; localized list names are common.
- The `completed` field is useful for filtering unfinished reminders when the list includes both done and pending items.
- Keep read-only inspection separate from edits so you can verify state before completing or deleting anything.
