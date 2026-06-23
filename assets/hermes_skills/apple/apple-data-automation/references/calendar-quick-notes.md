# Calendar quick notes

- For frequent read-only calendar requests (today / tomorrow / this week / this month), prefer the cached helper:
  - `python3 ~/.hermes/scripts/fast_calendar.py --range today --pretty`
  - `python3 ~/.hermes/scripts/fast_calendar.py --range week --pretty`
  - `python3 ~/.hermes/scripts/fast_calendar.py --range month --pretty`
- The helper defaults to common calendars `居家`, `工作`, `資料科學工作`, excludes `已排程的提醒事項` and `台灣節日`, and caches repeated read-only queries for 5 minutes.
- Use `--all-calendars` only when the user explicitly needs every Calendar.app calendar; use `--include-reminders` only as a read-only fallback when reminder-backed due items should appear with calendar events.
- For agenda requests that need both events and tasks, prefer separate source reads: Calendar.app for events, Reminders.app for reminders; merge them only in the final summary.
- Use `--no-cache` after creating/updating/deleting calendar events or when verifying fresh state.
- Prefer AppleScript bounded queries on a known calendar and small date window.
- Avoid full-history JXA scans across every calendar.
- When moving an event, set `endDate` before `startDate`.
- For recurring events, verify the first occurrence after creation.
- Re-query the target date/window after any edit.
