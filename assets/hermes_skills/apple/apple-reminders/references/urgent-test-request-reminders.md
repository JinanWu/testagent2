# Urgent test request reminders

Use this pattern when the user receives an ad hoc testing request and asks to write it into Apple Reminders.

## Capture exactly

Preserve operational details verbatim in the reminder body:
- requester / recipients to update (including Discord/Slack mentions if given)
- target environment and domain / URL
- test account, project, branch, or UAT label
- date range, route / airport / carrier / feature flags / query conditions
- expected fields, values, ranges, and boundary cases
- evidence requested: screenshot, actual values, error message, reproduction steps

## Reminder shape

Default to the user's primary list `提醒事項` unless they names another list. For same-day urgent work, use a high-priority title prefix `[P0]`, set built-in priority to 9, and due today 17:59 unless the user provides another deadline.

Body sections:
- 任務背景：who asked, why it matters, what must be verified.
- 要執行的內容：numbered, operational steps with exact URL/env/conditions.
- 預期產出：short reply/report with actual values and anomalies.
- 驗收標準：clear pass/fail statement the user can send back to the requesters.

## Verification

After writing, read back and report: list name, title, due date, priority, completed=false, and body length or a short body preview.
