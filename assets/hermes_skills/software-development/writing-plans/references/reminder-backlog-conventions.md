# Reminder backlog decomposition conventions

Use this when a user asks to turn a reminder item into smaller work items.

- First convert the item into 5-8 atomic tasks with clear boundaries.
- Put the most important or highest-risk steps first.
- Mark async/batched/non-blocking processing explicitly in the task title or note.
- Keep each item independently verifiable.
- If the user asks for a reminder update, prefer numbered bullets with short story-point labels such as `SP2`, `SP3`, `SP5`.
- Do not over-explain unless the user asks for a fuller plan.
- If there are separate outputs to build (for example, two trees, two reports, or two exports), split them into separate tasks before adding integration/upload steps.

## Large product/backlog sessions destined for Reminders

When the user is collaboratively defining a large product backlog and plans to turn it into Apple Reminders:

1. Do not create reminders immediately after the first brainstorm. First ask targeted scoping questions, then write a reviewable backlog document.
2. Use phases such as `P0`, `P1`, `P2`, `P3` to separate discovery, platform foundations, first user-visible features, later features, and governance/ops.
3. Each reminder should be a medium-grain deliverable, not a tiny implementation step: title format like `[P1][SP5] 設計 Agent 執行框架與 skill 調用流程`.
4. Put the detailed context in the reminder body: task background, execution content, expected output, acceptance criteria, and any confirmed decisions.
5. If the user confirms a different priority order than the source backlog, renumber reminder titles in the user-confirmed execution order while preserving the original backlog task number in the body.
6. For privacy/governance product work, explicitly record confirmed log visibility rules in the task body (for example, full input/output retained for debugging but admin-only).
7. After creating reminders, verify the target list count and first/last task titles, then update the backlog document status from “draft/to confirm” to “created/confirmed”.
