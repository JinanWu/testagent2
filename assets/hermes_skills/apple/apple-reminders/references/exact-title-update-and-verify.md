# Exact-title update and verification for user reminders

Use this pattern when the user asks to edit an existing reminder by its exact title, especially in the `提醒事項` list.

## Reliable workflow
1. If the user names a list, open that list by exact name; if they only give a title, search all lists and preserve the matched list name in verification.
2. Find reminders by exact title match when the user gave the full title. Do not assume the item is in `提醒事項`; project reminders commonly live in lists such as `心情指數儀表板` or `護照辨識新模型壓力測試`.
3. If exactly one reminder exists, update one field at a time when possible:
   - `completed = true` for completion
   - `dueDate = new Date(...)` for due date changes
4. If multiple exact-title matches exist across lists, report the matches and ask the user which one to update unless the intended list is obvious from the title/context.
5. Read back the reminder after writing and verify the fields, including the list name.

## Why this pattern
- Exact-title matching avoids accidentally updating similarly named reminders.
- A read-back verification step confirms the change actually stuck.
- For due dates, use the local timezone explicitly when constructing the `Date` object.

## Example JXA sketch
```javascript
const app = Application('Reminders')
const list = app.lists.byName('提醒事項')
const title = '看cablate的課程'
const matches = list.reminders().filter(r => r.name() === title)
if (matches.length === 0) throw new Error('not found')
const r = matches[0]
r.dueDate = new Date(2026, 5, 5, 12, 0, 0) // local time, month is 0-based
// r.completed = true
// verify:
// r.dueDate().toString()
// r.completed()
```

## Notes
- Prefer exact-name lookup for user-provided reminder titles.
- For the user's research-report reminders, keep verification output visible after writes.
- If the target item is already completed, read back first before changing anything else.
