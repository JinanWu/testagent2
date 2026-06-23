---
name: cronjob-management
description: Use when inspecting, creating, updating, pausing, resuming, or removing Hermes cron jobs, especially recurring monitoring jobs with delayed starts. Keep job IDs exact, verify schedule semantics, and summarize the live job state clearly.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [cronjob, scheduler, recurring-jobs, monitoring, devops]
    related_skills: [webhook-subscriptions, kanban-orchestrator]
---

# Cronjob Management

## Overview

Use this skill when the user wants you to inspect or modify a scheduled Hermes job. The important part is not just editing a timer; it is preserving the user's intent across schedule, repeat behavior, delivery target, and the job's prompt.

Cron jobs in Hermes are self-contained. A job runs in a fresh session, so the prompt must explain everything the future run needs without assuming the current conversation exists. For monitoring jobs, the prompt should say what to check, when to stay silent, when to report, and what the expected output shape is.

This skill emphasizes three things:

1. Always inspect the live job state before changing or removing anything.
2. Treat the cron expression, ISO timestamp, repeat mode, and prompt body as separate knobs.
3. After any update, restate the effective next run time and whether the job is still recurring.

Reference notes and examples: `references/cronjob-management-notes.md`.
Job update safety checklist: `references/job-update-safety.md`.
Discord delivery smoke-test pattern: `references/discord-delivery-smoke-tests.md`.
SCM watchlist target-list migration example: `references/scm-reminders-watch-pattern.md`.
Script-only monitor verification recipe: `references/script-only-monitoring-verification.md`.

## When to Use

- The user asks what scheduled jobs exist.
- The user asks to change a job's schedule, cadence, pause state, delivery target, or prompt.
- The user asks what a specific scheduled task does.
- The user asks to stop, resume, or delete a job.
- The user asks for a delayed-start monitoring job, e.g. “start after date X, then run daily”.
- The user wants a human-readable summary of a job's configuration.

Do not use this skill for one-off manual tasks with no recurring schedule.

## Core Workflow

### 1) Inspect first

Before modifying or removing a job:

- List jobs with `cronjob(action='list')`.
- Identify the exact `job_id` from the live output.
- Do not guess job IDs from memory or from a previous turn.

The list output is the source of truth for:

- `name`
- `job_id`
- `schedule`
- `repeat`
- `deliver`
- `enabled` / `paused`
- `next_run_at`
- `last_run_at`
- `last_status`
- `enabled_toolsets`

### 2) Confirm the intent

Translate the user's request into one of these operations:

- create a new job
- update schedule / prompt / metadata
- pause a job
- resume a job
- remove a job
- run now
- inspect only

If the user says “change it after a certain date” or “start after the 21st”, decide whether they mean:

- a one-time delayed first run, or
- a recurring job whose prompt suppresses output until the threshold date

Those are different behaviors and should be reflected explicitly in the result.

### 3) Update carefully

When updating an existing job:

- Keep the `job_id` exact.
- Change only the fields needed.
- If the job is recurring, verify that `repeat` still matches the user's expectation after the update.
- If the schedule is changed to a one-shot timestamp, call that out clearly.
- If the prompt is used to gate output by date, ensure the prompt says so plainly.

### 4) Verify after the change

Always confirm the resulting job state from the tool output:

- next run time
- whether it is recurring
- whether it is enabled
- the delivery target
- the prompt preview / intent

For short-delay delivery tests, create the job, then immediately list jobs to verify `next_run_at` and state, and after the due time re-list to confirm `last_run_at` / `last_status` instead of assuming creation implies delivery. See `references/delivery-smoke-test.md` for the smoke-test pattern.

If the result looks surprising, pause and inspect again before telling the user it is correct.

## Reading Existing Jobs

The job list gives you a compact overview, but sometimes the prompt is truncated. If the user asks what the task content is:

- use the live cronjob list output first
- if the preview is insufficient, use session recall to recover the original creation context
- summarize the task in user-friendly language rather than dumping every internal detail

Be careful to separate:

- the schedule metadata
- the prompt body
- any data source the prompt references

## Delayed-Start Monitoring Jobs

A common pattern is “run daily, but do not report until a cutoff date.” There are two valid implementations:

1. **Prompt-gated silence**
   - Keep the recurring schedule.
   - Make the prompt explicitly say not to produce output until a date threshold.
   - Good when you want the job to keep its cadence, but stay quiet until the threshold.

2. **Schedule-gated start**
   - Set the first run to the first desired date/time.
   - Keep or adjust recurrence only if the scheduler semantics clearly preserve it.
   - Good when the job should literally not start until later.

Pitfall: do not mix these up. If the user asked for a recurring job after a date, make sure the final explanation states whether the delay is enforced by the prompt or by the schedule.

## Job Prompt Writing

For recurring monitoring jobs, the prompt should answer these questions:

- What data/source is being checked?
- What is the exact condition being validated?
- When should the job stay silent?
- What should it report when the condition fails?
- What is the desired output shape?

A good prompt is self-contained and operational, for example:

- data source
- table name
- environment
- condition to test
- threshold
- reporting rule
- silence rule

Avoid prompts that assume the future run remembers the conversation.

## Script-Only Monitoring Jobs

When a recurring monitor does not need LLM reasoning, prefer `no_agent=True` with a script. This is especially important for high-frequency checks such as every 15 minutes. Design the script so stdout is the delivery contract:

- Emit a complete human-readable message only when the user should be notified.
- Emit nothing when the job should stay silent; empty stdout means no delivery.
- Persist small state files to avoid duplicate alerts, e.g. same symbol/event/date.
- Put time gates and stale-data checks inside the script when schedule expressions are broad.
- Keep the cron prompt minimal and explicit that no LLM reasoning is needed.

For maintainability, avoid scattering config, state, and business logic under `~/.hermes/scripts/`. Keep the real implementation in a standalone project directory with clear `config/`, `data/`, `src/`, `scripts/`, and `logs/` folders. Because Hermes cron scripts must live under `~/.hermes/scripts/`, place only thin wrapper scripts there; the wrappers should `exec` the project’s real script/CLI. Cron stays responsible for timing, while the project owns logic and tests.

When verifying a script-only monitor, treat these as separate questions:
- Did the cron job run without crashing?
- Did the script emit non-empty stdout?
- Did the upstream data source actually return usable records?
- Did the business rule produce a notification-worthy event?

A job can be `ok` with empty stdout if the script intentionally stayed silent. That is not evidence of a false alert; it is only evidence that the run did not error.

See `references/data-source-verification.md` for a compact checklist on verifying upstream data coverage before declaring a script-only monitor healthy.

## Typical Update Patterns

### Change cadence only

- List jobs
- Update `schedule`
- Verify `next_run_at`
- Tell the user whether `repeat` remained unchanged

### Change prompt only

- Keep schedule and delivery as-is
- Update the prompt so the monitoring condition is explicit
- Verify the prompt preview if the tool exposes it

### Pause / resume

- Use the live `job_id`
- Confirm whether the state becomes paused or scheduled
- Mention any impact on `next_run_at`

### Remove

- List first
- Remove using the exact `job_id`
- Never guess based on name alone

## Common Pitfalls

1. **Guessing the job ID.**
   Always list first.

2. **Treating schedule and prompt as the same thing.**
   A delayed-start can be enforced by either one, and the user may care which.

3. **Using interval shorthand when the user wants recurring monitoring.**
   `schedule='10m'` creates a one-shot job (`repeat: once`). For recurring progress checks, create the job with `schedule='every 10m'` from the start and verify the resulting job shows `repeat: forever`. If an update from `10m` to `every 10m` leaves `repeat: once`, remove the mistaken job and recreate it rather than telling the user it is recurring.

4. **Forgetting to restate recurrence.**
   If the schedule is changed to a one-time timestamp, say so clearly. For recurring monitors, explicitly state both cadence and `repeat: forever` after verification.

5. **Leaving the prompt ambiguous.**
   Monitoring jobs should say exactly what counts as pass/fail and when to stay silent.

6. **Removing the wrong job because names are similar.**
   Verify `job_id`, `name`, `prompt_preview`, `schedule`, `repeat`, `deliver`, and `script` together.

7. **Updating the wrong similar job.**
   If two jobs are closely related or one was previously modified by mistake, re-list immediately after the update and confirm both the intended job and the restored job by `job_id` and live fields. Do not assume the job name alone proves correctness.

8. **Editing only the cron prompt when the real behavior lives in an external script.**
   If the job runs a script, inspect and update the script too when changing destination lists, filters, or output text. Verify the live behavior from both the cron definition and the script source.

9. **Assuming the current chat context is available to the future run.**
   It is not; the job prompt must stand alone.

10. **Treating `last_status=ok` as proof of data availability.**
   For monitors that depend on external feeds, confirm the upstream source directly when a run is unexpectedly silent or only partially populated. A successful run can still have no usable records if the source returned placeholder values (for example `z='-'`/`pz='-'`).

## Verification Checklist

- [ ] I listed current jobs before changing anything.
- [ ] I used the exact `job_id` from the tool output.
- [ ] I confirmed whether the job is recurring or one-shot after the update.
- [ ] I checked `next_run_at` after changing the schedule.
- [ ] I described the job contents in plain language when the user asked.
- [ ] I treated delayed-start semantics explicitly, not implicitly.

## Quick Recipes

### Inspect all current jobs

1. `cronjob(action='list')`
2. Read `name`, `job_id`, `schedule`, `repeat`, `next_run_at`, `enabled`, and `prompt_preview`
3. Summarize the current live state to the user

### Update a recurring job's start date

1. List jobs
2. Identify the target `job_id`
3. Update the schedule or prompt according to the user's requested semantics
4. Re-read the live output
5. Explain the effective behavior back to the user

### Remove a job safely

1. List jobs
2. Confirm the exact target by name and prompt preview
3. Remove using `job_id`
4. Report the deletion result
