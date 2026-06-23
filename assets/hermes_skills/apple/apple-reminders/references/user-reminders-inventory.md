# Unfinished-reminders inventory

When the user asks what is still unfinished in the main Reminders list, use an exact-name lookup for `提醒事項` and filter to `completed == false`.

Recommended read-only probe (JXA / osascript):
- Resolve the list by exact name.
- Return only reminders where `completed()` is false.
- Preserve the reminder title and due date if present.
- If the list is missing, return the available list names instead of guessing.

Useful output shape:
- title
- completed
- due date (ISO string or null)

Default behavior for inventory requests:
- If the user asks for "未完成" / "還有哪些" / "剩下哪些", omit completed reminders unless they explicitly ask for the full list.
- If a due date exists, keep it visible because it helps prioritize the remaining items.
