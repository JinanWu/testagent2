# Apple Reminders priority + bulk write notes

Session-derived notes for complex project backlog creation.

## Importance / priority mapping
When a reminder title is prefixed with importance labels, mirror the label in the built-in `priority` field too.

Practical mapping that worked:
- `P0 -> priority 9`
- `P1 -> priority 5`
- `P2 -> priority 1`

## Bulk create / update pattern
Use `osascript -l JavaScript` and exact-name matching:
1. Find or create the target list.
2. For each item, locate an existing reminder with the exact title.
3. If found, update title/body/priority/completed.
4. If not found, create it under the target list.
5. After the write, verify:
   - list exists
   - reminder count matches expectation
   - sample titles look correct
   - built-in `priority` values match the prefix

## Useful JXA shape
- `var list = R.lists.byName("護照辨識壓力測試");`
- `var r = R.make({new: "reminder", at: list, withProperties: {name: title}});`
- `r.body = body;`
- `r.priority = 9;`
- `r.completed = false;`

## Verification snippet
Read back a few items with:
- title
- `priority()`
- `completed()`
- `body()`
