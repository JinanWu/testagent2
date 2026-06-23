# Multi-repo architecture inspection playbook

Use this when a user provides several related repositories and wants the system architecture reconstructed quickly.

## Fast workflow
1. Clone all repos shallowly into a temp directory.
2. Inventory each repo's top-level files and package layout.
3. Read these first:
   - README.md
   - main.py / entrypoint
   - config files (yaml/toml/env examples)
   - Dockerfile
   - cloudbuild / CI config
4. Identify the data-flow chain:
   - trigger/scheduler -> crawler/job -> raw storage -> ETL/transform -> downstream DB/service
5. Summarize by repo and then as one system view.

## Heuristics that help
- Historical names can be misleading. Treat repo/job names as labels, not facts.
- The core source is usually obvious from the entrypoint + README, not from the repo name.
- If one source has the richest schema and others look like subset feeds, treat that source as the canonical/primary feed until proven otherwise.
- For ETL repos, look for extractor / transform / loader modules and source-specific transformer files.

## What to extract into the final map
- repo name
- role (crawler / ETL / support)
- trigger method
- storage target(s)
- downstream consumer(s)
- known fragile points / external dependencies
- ownership boundaries (what the user owns vs outside teams)

## Example shape
- multiple crawler repos write to BigQuery
- one ETL repo reads from BigQuery and writes a merged table to Cloud SQL
- Cloud Scheduler triggers Cloud Run Jobs on a daily cadence
- downstream backend/frontend teams consume only the Cloud SQL layer
