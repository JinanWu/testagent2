# Delivery smoke-test notes

Observed pattern for testing scheduled delivery in-chat:

- Creating a cronjob only proves the job was accepted and scheduled.
- Immediately after creation, inspect the live job state with `cronjob(action='list')` to confirm:
  - `next_run_at`
  - `enabled` / `state`
  - `repeat`
  - `deliver`
- For a one-shot test, use an exact ISO timestamp in the future and keep the prompt minimal (e.g. `到點時請直接回覆：時間到`).
- If the user needs a guaranteed visible one-minute countdown, a background one-shot timer can be used as a fallback smoke test, but it does not test cron scheduling itself.
- After the scheduled time passes, re-list jobs and verify `last_run_at` or `last_status` rather than assuming delivery succeeded from creation alone.
