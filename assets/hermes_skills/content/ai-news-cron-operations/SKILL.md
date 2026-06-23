---
name: ai-news-cron-operations
description: Use when creating, updating, or inspecting scheduled Hermes jobs for AI Lab News and AI智報 production.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [ai-news, cron, scheduling, operations]
    related_skills: [ai-news-workflow, ai-news-source-recon]
---

# AI News Cron Operations

## Overview

This skill defines scheduling rules for AI Lab News / AI智報 jobs. It exists because the user works mainly from a portable MacBook that may sleep outside office hours.

## When to Use

Use this skill when:

- Creating or updating AI newsletter cron jobs.
- Checking schedule suitability.
- Writing self-contained cron prompts.
- Recovering from missed newsletter jobs.

## Availability Window

The user's computer is usually on during work hours:

`10:30-18:00`

Prefer schedules inside this window. Avoid early morning, late evening, or overnight jobs.

## Recommended Schedule

- Morning collect: Monday-Friday 10:45 — `45 10 * * 1-5`
- Midday collect: Monday-Friday 13:30 — `30 13 * * 1-5`
- Afternoon collect + normalize: Monday-Friday 16:00 — `0 16 * * 1-5`
- AI Lab News daily draft: Monday-Friday 16:45 — `45 16 * * 1-5`
- AI智報 weekly draft: Friday 17:15 — `15 17 * * 5`

## Cron Prompt Rules

Every cron prompt must be self-contained. Include:

- Root path: `/Users/wujinan/Documents/AI-News/`
- Source registry path.
- Output paths.
- Date/week handling.
- Silence/failure behavior.
- Review-only rule: create drafts, never publish.

## Delivery Rules

Draft jobs should report back to the origin/current chat thread with:

- Draft path.
- Short summary of included items.
- Any source failures.
- Reminder that user review is required.

Collection jobs may stay quiet unless they fail or are explicitly configured to report.

## Reliability Rules

- Jobs must be safe to rerun.
- Use seen URL/title indexes for dedupe.
- Missed daily jobs should not break weekly generation.
- Source failures should be logged, not fatal.

## Verification Checklist

- [ ] Job runs inside 10:30-18:00.
- [ ] Prompt is self-contained.
- [ ] Output path is explicit.
- [ ] Job does not publish automatically.
- [ ] User review is required before final.
