# Job Update Safety Checklist

Use this when updating Hermes cron jobs that have similar names or overlapping purposes.

## Why this matters
A past update accidentally targeted the wrong job first, then had to restore the original job and reapply the desired change to the correct one. The lesson is not about a specific job; it is to verify the live state before and after every update.

## Minimal safe sequence
1. List jobs first.
2. Identify the target by exact `job_id`, not by name alone.
3. Compare `name`, `prompt_preview`, `schedule`, `repeat`, `deliver`, `script`, and `enabled_toolsets`.
4. Update only the intended job.
5. Immediately list jobs again.
6. Confirm the intended job changed and any unrelated job did not.
7. If the wrong job was touched, restore it first, then re-run the intended update.

## When two jobs are similar
Treat similar names as a hazard. Do not rely on memory of the “obvious” job. Verify all of these together:
- `job_id`
- `name`
- `prompt_preview`
- `schedule`
- `deliver`
- `script`
- `state` / `enabled`

## Good verification habit
After any cron edit, restate:
- what changed
- what stayed the same
- the next run time
- whether the job is recurring or one-shot

## Restore pattern
If you temporarily changed the wrong job:
1. Restore it to the previous schedule/delivery/prompt.
2. Re-list jobs.
3. Apply the intended change to the correct job only.
4. Verify both jobs again.
