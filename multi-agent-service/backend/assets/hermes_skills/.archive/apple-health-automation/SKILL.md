---
name: apple-health-automation
description: Design, troubleshoot, or automate Apple Health data export and daily summarization flows using Shortcuts, Health exports, files, and companion apps.
---

# Apple Health Automation

Use this skill when the user wants to automatically export, summarize, or inspect Apple Health / HealthKit data, especially for recurring tracking like steps, sleep, heart rate, workouts, and weight.

## Core goal
Turn Health data into a small, repeatable, human-readable artifact that can be reviewed later or passed to another assistant for analysis.

## Recommended approaches
1. Prefer a daily compact summary over raw Health export XML.
2. For recurring monitoring, generate a fixed format such as CSV or JSON.
3. Store output somewhere stable and easy to retrieve:
   - iCloud Drive
   - Files app
   - Notes
   - a plain-text file with one record per day
4. If the user wants automation on iPhone, start with Shortcuts / automations first; if they want deeper processing, use a file-based pipeline.

## Typical workflow
1. Identify which metrics matter most.
   - Steps
   - Distance
   - Sleep
   - Heart rate
   - Workouts
   - Weight
2. Decide the output shape.
   - One line per day is usually best for long-term tracking.
   - Keep fields stable across runs.
3. Choose the export mechanism.
   - Shortcuts for scheduled export or on-demand summaries
   - Third-party Health export/automation apps if the user wants less setup
   - Manual Health export XML only for one-off inspection or backfill
4. Verify the result by checking a few sample days, not a full dump.
5. If the user wants interpretation, summarize trends rather than paste the raw data.

## Pitfalls
- Raw Health export XML is usually too large and inconvenient for regular analysis.
- Averages can hide missing days or outliers; inspect the date coverage first.
- If multiple devices contribute data, confirm which source produced the values before drawing conclusions.
- Avoid overengineering: if the user only needs step counts and sleep, do not build a broad schema.
- Keep outputs compact so the user can easily paste them into chat.

## Output formatting guidance
When presenting a proposed setup, be concise and practical:
- State whether the approach is feasible.
- Recommend the simplest reliable path.
- Include the exact fields to export.
- If relevant, note whether the result should be stored as CSV, JSON, or plain text.

## Research notes
See `references/healthkit-export-notes.md` for a condensed session note and external discovery summary.
