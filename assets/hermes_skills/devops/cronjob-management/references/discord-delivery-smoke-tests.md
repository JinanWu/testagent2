# Discord delivery smoke tests

Use this when the user wants to verify that a scheduled Hermes job can deliver a message to Discord.

## Pattern

- Keep the prompt minimal and deterministic.
- Use the exact user-facing text that should appear in Discord.
- Prefer a one-shot schedule for pure delivery verification.
- Immediately after creating the job, inspect the live job list to confirm:
  - `next_run_at`
  - `enabled` / paused state
  - `repeat`
  - `deliver`
- After the scheduled time passes, re-list jobs and confirm `last_run_at` / `last_status` rather than assuming delivery succeeded from job creation alone.

## Good prompt shape

- "到點時請直接回覆：<exact text>"
- Avoid extra reasoning, formatting, or optional branches.

## What to report back

- Whether the job was accepted.
- Whether it is one-shot or recurring.
- The effective delivery target.
- The observed result after the run window.
