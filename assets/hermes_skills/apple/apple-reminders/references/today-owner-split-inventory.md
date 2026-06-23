# Today reminders split by owner

Use when the user asks for today's unfinished tasks and wants them separated between the user and another owner (e.g. 偉甄).

## Read flow

1. Query unfinished reminders due today across all lists, not just the primary `提醒事項` list.
2. Preserve list name, title, due time, priority, and body/notes for owner detection.
3. Classify owner from the reminder body first:
   - `負責人：偉甄` or similar explicit owner line → 偉甄.
   - Other explicit owner names → that owner.
   - No explicit delegated owner → user's task by default.
4. If the title/list suggests a project but the body has no owner, do not infer a delegate unless the body says so.
5. For each section, keep the output short and operational: title, list, due/overdue status, and one sentence of what it means.

## Output shape

```text
現在 YYYY/MM/DD HH:MM。

你的任務
- HH:MM List｜Title — short action / recommended next step

偉甄的任務
- HH:MM List｜Title — short action / recommended next step
```

## Pitfalls

- Do not treat all items in a delegated project list as the delegate's work. Use the body owner line when available.
- Do not include tomorrow or rescheduled items when the user says「今天還沒完成」.
- If a task was just rescheduled out of today, exclude it from the today list even if it was mentioned earlier in the conversation.
