# Work schedule planning with Apple Reminders

Use this pattern when the user wants to plan daily/weekly work from Calendar + Reminders.

## Interaction style

- Keep the plan structured by project/workstream first, then by day/time. Avoid a scattered mix of calendar items, reminder inventories, and recommendations.
- If the user says the plan feels scattered, immediately narrow scope to the active workstream and list only the relevant reminders before proposing times.
- Use concise sections: `目前剩下什麼` → `完成定義` → `建議順序` → `要寫進提醒事項的項目`.
- For schedule changes, update Apple Reminders first, then verify due dates and counts.

## Planning sequence

1. Pull only the relevant reminders/lists for the active workstream; do not re-inventory every list unless needed.
2. Ask/confirm ownership and deadline assumptions if they affect due dates or assignment.
3. Convert the user's spoken plan into reminder bodies with:
   - 任務背景
   - 要執行的內容
   - 預期產出
   - 驗收標準
   - 排程與負責人, when applicable
4. For delegated work, write the owner explicitly in the body, e.g. `負責人：偉甄`.
5. For focused batch work, encode the rationale: `集中處理，避免任務切換造成耗時`.
6. If the user gives a deadline like `週三以前完成`, prefer concrete due times on the planned workdays plus mention the outer deadline in the body.
7. After writes, verify:
   - list exists
   - unfinished item count
   - titles
   - due dates
   - priority mapping
   - key body markers such as owner/deadline text

## Useful due-date conventions from this session

- If a workstream should be finished before a meeting, set due shortly before the meeting, e.g. `13:59` before a `14:00` discussion.
- If a batch is expected by noon, set test items to `12:00`.
- For end-of-workday completion, use `17:59` rather than `18:00`.

## Emotional overload / postponement planning

When the user says their emotional state is poor and asks to push work back:

1. First inventory Calendar + Reminders for the requested window, but separate the user's own workload from delegated work.
   - If the user clarifies that a workstream is owned by someone else (e.g. `偉甄做`), keep those reminders in place and remove them from the user's personal overload calculation.
   - Keep meetings that only require attendance/decision-making separate from hands-on implementation tasks.
2. Ask or infer location constraints before moving tasks across workdays.
   - If the user will not be at the company, do not schedule tasks that require company environment, data, devices, or permissions on that day.
   - Use offsite days for low-pressure planning, reading, or scoping work.
3. Prefer a recovery-shaped plan:
   - Today: only necessary meetings / communication; avoid new implementation commitments.
   - Weekend/offsite: at most light planning or learning, not hard deliverables.
   - Next workday in office: environment-dependent tests and submissions.
4. When the user approves the plan (`幫我這樣排吧`), immediately update Reminders and verify due dates by reading them back.

## Adding a new project workstream into an already busy week

When the user asks to add several related tasks after reviewing next week's calendar/reminders:

1. Check the week first and identify overloaded days before placing new work.
2. Keep the new workstream project-first in the relevant list, using structured reminder bodies.
3. Sequence discovery and implementation gates explicitly:
   - discussion / implementation approach
   - find and evaluate model/options
   - small-sample validation
   - full rerun / batch execution
   - durable ETL or pipeline change
4. Avoid placing new strategic work on a day that already has multiple P0/P1 reminders; start the new sequence on the next viable focused slot.
5. Use concrete due times (`12:00`, `17:59`, meeting time) and verify that each created reminder has the structured body markers (`任務背景`, `要執行的內容`, `預期產出`, `驗收標準`).

## Pitfall

Do not over-explain a broad plan before making the user's active project clear. The user prefers a tidy, project-first plan they can discuss and refine, not a long mixed inventory.

Do not assume every reminder in a project is the user's personal work. Delegated tasks should stay scheduled for the assignee unless the user asks to move them.

Do not schedule environment-dependent work onto offsite/weekend days just because the calendar is open; respect whether the task requires the company environment.