# Apple Reminders JXA fallback

Use `osascript -l JavaScript` when `remindctl` is unavailable or when you want compact read-only inspection.

## List reminder list names

```bash
osascript -l JavaScript -e 'var Reminders = Application("Reminders"); Reminders.lists().map(function(l){ return l.name(); }).join("\n");'
```

## List reminders in one list as JSON

```bash
osascript -l JavaScript -e 'var Reminders = Application("Reminders"); var list = Reminders.lists.byName("提醒事項"); JSON.stringify(list.reminders().map(function(r){ return {name: r.name(), completed: r.completed(), dueDate: r.dueDate() ? r.dueDate().toString() : null}; }));'
```

## Notes

- Prefer JXA for quick inspection; it is easier to quote safely than multiline AppleScript heredocs.
- Keep samples small; when verifying a task, inspect only the target list and a few reminders.
- For write operations, still prefer the staged create-then-verify workflow described in `references/apple-reminders-reliable-editing.md`.
