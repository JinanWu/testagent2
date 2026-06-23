# Apple Reminders JavaScript fallback recipes

Use `osascript -l JavaScript` when `remindctl` is unavailable or when you want reliable structured inspection/editing without shell-quoting headaches.

## List reminder list names

```bash
osascript -l JavaScript -e 'var R = Application("Reminders"); R.lists().map(function(l){ return l.name(); }).join("\n");'
```

## List reminders in a specific list with status

```bash
osascript -l JavaScript -e 'var R = Application("Reminders"); var list = R.lists.byName("提醒事項"); JSON.stringify(list.reminders().map(function(r){ return {name: r.name(), completed: r.completed(), dueDate: r.dueDate() ? r.dueDate().toString() : null}; }));'
```

## Mark a reminder complete by exact name

```bash
osascript -l JavaScript -e 'var R = Application("Reminders"); var list = R.lists.byName("提醒事項"); var rs = list.reminders(); var target = rs.filter(function(r){ return r.name() === "確認 prod-cola-rd BigQuery Data Editor 權限是否可用"; })[0]; if (!target) throw new Error("reminder not found"); target.completed = true;'
```

## Notes

- Prefer exact-name matching for completion when the list is small and the title is unique.
- For writes, verify by reading back `completed()` or `dueDate()` after the operation.
- `osascript -l JavaScript` is a good default for AppleScript fallback because it returns structured values more predictably than ad hoc multiline AppleScript in shell heredocs.
