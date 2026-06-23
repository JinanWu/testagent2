# Calendar + Reminders agenda separation

Use when the user asks for an agenda such as「今天/明天/本週有什麼安排」and expects both Calendar events and Reminders due items.

## Principle

Keep sources separate:

- Calendar.app = scheduled events / meetings / appointments.
- Reminders.app = tasks / to-dos / deadlines.
- Do not rely on Calendar.app's `已排程的提醒事項` as the primary Reminders source; it is a reminder-backed calendar view, not the Reminders data model.

## Recommended read flow

1. Query Calendar.app first using the bounded fast calendar helper for the requested date window.
2. Query Reminders.app separately for due items in the same window.
3. Merge only in the final human-facing summary, clearly separating「行事曆」and「提醒事項 / 待辦」.

## Reminders read performance

For Reminders read-only agenda queries:

- Start lightweight: list, title, due date, priority, flagged.
- Do not read long `body` / notes unless the user asks for details of specific items.
- Prefer the primary list `提醒事項` first when the user did not ask for every project list.
- If the user asks for all project tasks, query list summaries first, then expand selected project lists.

## Fallback

If direct Reminders.app Apple Events are too slow or unavailable for a read-only agenda, Calendar.app's `已排程的提醒事項` can be used as a fallback only for due-date visibility. Label this as a fallback because it may omit Reminders-specific metadata such as list, notes/body, completion metadata, and priority semantics.

## Summary format

Use a compact two-section output:

```text
今天 YYYY/MM/DD

行事曆：
- HH:MM–HH:MM  title

提醒事項 / 待辦：
- HH:MM  title

明天 YYYY/MM/DD
...
```

Keep caveats short. Only mention the fallback if it affected the result.