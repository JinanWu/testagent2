# User-specific Apple Reminders workflow notes

- The user's default/primary Reminders list is `提醒事項`.
- For relative deadlines phrased as `before 6pm` / `下午六點前`, convert to an exact due time of `17:59` on the target day unless the user specifies another minute.
- When the user says `today before noon`, use `11:59` as the concrete due time.
- When the user gives a clock time plus an evening cue like `晚上` / `pm` (for example, `今天十一點半` followed by `晚上`), interpret it as the PM version of that time and set the corresponding exact 24-hour due time (e.g. `23:30` for `11:30 PM`) unless the user explicitly asks otherwise.
- When the user asks whether a task exists, first check the primary list for an exact title match, then surface closely related reminder titles, and include completion status and due date so the user can decide quickly.
- For weekly planning sessions, use a simple Scrum-like cadence: review unfinished reminders, split them into backlog / this week / today, and estimate each work item with a title prefix such as `[P1][SP3]`.
- When creating reminders through AppleScript, a reliable pattern is:
  1. Create the reminder first.
  2. Set `body` separately.
  3. Set `due date` separately.
  4. Verify with `properties of r` or a follow-up lookup.
- If a list lookup or one-shot property assignment behaves oddly, split creation and property assignment into separate steps before retrying.
