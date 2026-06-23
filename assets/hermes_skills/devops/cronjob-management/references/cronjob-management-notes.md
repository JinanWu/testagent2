# Cronjob Management Notes

This reference captures a few durable patterns observed while working on Hermes cron jobs.

## Live job inspection pattern

- Always call `cronjob(action='list')` before updating or removing a job.
- Treat the returned `job_id` as the only safe identifier.
- Use `name`, `schedule`, `repeat`, `next_run_at`, `enabled`, and `prompt_preview` together to confirm you have the right job.

## Delayed-start monitoring jobs

Two ways to implement a delayed start:

1. Prompt-gated silence
   - Keep the recurring schedule.
   - Prompt says not to emit anything until the cutoff date.
   - Useful when cadence should already exist, but reporting should stay quiet.

2. Schedule-gated start
   - Set the first run time to the first allowed datetime.
   - Verify whether the scheduler preserves recurrence after the update.
   - Useful when the job must not start earlier at all.

Always tell the user which one you used.

## What to say when the user asks for job contents

- If the prompt preview is enough, summarize the job in plain language.
- If the preview is truncated, recover the original creation context via session recall instead of guessing.
- Separate the job's schedule from the job's actual check logic.

## Example from this session

A cron job named `cola-dev-formula-type-check` was listed, then updated to start later. The user then asked to see the task content. The useful summary was:

- It checks Cola dev BigQuery data every day at 11:00 Asia/Taipei.
- It validates whether the latest day's data contains `公式類型 = 2`.
- It checks whether that value accounts for at least 3% of rows.
- It was intended to stay silent before the date threshold.

This is a good example of a self-contained monitoring prompt: data source, condition, threshold, and silence rule were all explicit.
