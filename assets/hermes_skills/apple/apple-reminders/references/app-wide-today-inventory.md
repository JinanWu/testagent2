# App-wide Today Inventory

This note captures the reminder-reading pattern used when the user asks for「今天的提醒事項」or the app-wide Today view.

## Rule
- Do not limit the query to a single list such as `提醒事項` unless the user explicitly says so.
- Query the whole Reminders app for reminders due today across all lists.

## Reliable JXA pattern
1. Fetch all list names first.
2. Resolve each list by name again inside the loop (`app.lists.byName(listName)`) instead of reusing list object references across iterations.
3. Use a bounded query for unfinished reminders due today (`completed:false`, `dueDate` in the local today range).
4. Return only lightweight fields for planning: list name, title, due date, priority.

## Why
A full cross-list scan can time out or fail with `Error: 無法取得物件。` if the script holds stale list references or tries to read too much per reminder.

## Planning output shape
For daily planning, keep the result compact:
- list
- title
- dueDate
- priority

Defer long reminder bodies/notes until the user selects items to execute.