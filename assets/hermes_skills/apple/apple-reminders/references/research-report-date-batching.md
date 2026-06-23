# Randomly batch undated research-report reminders onto future days

Use this when the user wants undated research-report reminders spread out in small daily batches, e.g. 3 items per day.

## Known-good flow

1. Read the target list (`提醒事項`).
2. Filter to unfinished reminders whose title contains `研究報告` and whose `dueDate()` is empty.
3. Shuffle the filtered reminders in-memory.
4. Start the schedule at tomorrow 00:00 local time.
5. Assign due dates in buckets of 3 reminders per day:
   - items 1–3 => tomorrow
   - items 4–6 => the day after tomorrow
   - etc.
6. Verify:
   - the updated reminders now have due dates
   - no unfinished `研究報告` reminders remain without a due date

## JXA notes

- `reminder.dueDate = someDate` works reliably for setting the date.
- After writing, read back `reminder.dueDate()` to verify the change.
- For date-only reminders, zero the time first with `setHours(0,0,0,0)`.

## Pitfall

Don't assign one reminder at a time ad hoc when the user asks for a grouped spread. Always compute the full shuffled set first so the 3-per-day pattern stays consistent after retries.
