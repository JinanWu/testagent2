# Today / tomorrow agenda recipe

Use this when the user asks for a compact personal schedule summary across Calendar and Reminders.

Workflow:
1. Calendar first: use `python3 ~/.hermes/scripts/fast_calendar.py --range today --pretty` and `--range tomorrow --pretty` for read-only summaries.
2. If the user explicitly wants every calendar, use `--all-calendars`; otherwise keep the default calendar set to avoid slow full scans.
3. Do not rely on full-history JXA scans across every calendar for agenda questions; they are prone to timing out on large accounts.
4. Reminders: use an exact-name lookup for `提醒事項` and filter to `completed == false`.
5. For reminders due today/tomorrow, preserve title, due date, priority, and flagged state.
6. Present the final answer as two short sections: today / tomorrow, then Calendar and Reminders.

Useful reminder probe shape (JXA):
- resolve `Application('Reminders').lists.byName('提醒事項')`
- iterate reminders only within that list
- keep only reminders where `completed()` is false and `dueDate()` exists
- compare dates by day boundary, not by string matching
