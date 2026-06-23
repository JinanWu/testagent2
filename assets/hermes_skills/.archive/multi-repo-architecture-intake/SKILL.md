---
name: multi-repo-architecture-intake
description: "Turn a user-described multi-repo/data-platform into a living system map, with repo/environment/table/job relationships and verification checkpoints."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [architecture, multi-repo, data-platform, gcp, bq, cloud-run, scheduler, system-mapping]
---

# Multi-Repo Architecture Intake

Use this skill when a user is introducing a non-trivial system made of multiple repositories, environments, scheduled jobs, and data stores. The goal is to quickly build and maintain a shared mental model so later requests can be answered without re-asking for the same structure.

- When the user describes a company-wide AI tool management platform, start by mapping the control plane: tool registry, approval workflow, role-based access, audit trail, and admin/reporting surface.
- A strong first cut is to integrate the team's self-developed tools first, because they define the platform's most controllable contract and governance workflow.
- Treat the chat surface (Discord, web, CLI) as an entry channel, not the platform itself; the real platform is the orchestration + governance layer behind it.

## Core workflow

1. Identify the class of system
   - What is the product or domain?
   - What is the end-to-end flow of data or requests?
   - What is the authoritative source of truth?

2. Collect the stable nouns
   - Repos
   - Jobs/entrypoints
   - Environments (dev/prod/staging/etc.)
   - Schedulers / triggers
   - Data stores and tables
   - Downstream consumers
   - External dependencies owned by other teams

3. Build the map in layers
   - Layer 1: human summary of the product
   - Layer 2: repo-to-role mapping
   - Layer 3: runtime flow (trigger -> transform -> load)
   - Layer 4: environment differences
   - Layer 5: risk points and dependencies

4. Verify by reading evidence
   - Prefer repo README, entrypoint, config, and deployment files
   - For GCP/BQ systems, confirm with CLI output rather than assumptions
   - If the user names a table/job/env, verify the exact string before acting

5. Keep a living summary
   - When a project becomes clear, store a compact reference summary under this skill's `references/` directory
   - Update that reference as new repos, tables, or environments are introduced

## Recommended output format

When reporting the map back to the user, prefer a compact structure:

- Product goal
- Repos and their roles
- Runtime flow
- Environments
- Data stores and tables
- Schedulers / triggers
- Main risks
- Open questions

## Working with repo inspection

If the user gives GitHub URLs or local repo paths:
- inspect the repository root, README, entrypoint, config, Dockerfile, and CI files first
- identify the main process and how it gets inputs/outputs
- if multiple repos look similar, compare naming conventions and package structure
- do not assume repo names are self-explanatory; verify the actual runtime role
- for privacy-sensitive AI/API repos, explicitly check whether inputs, model outputs, or recognized fields are written to files, databases, logs, test output, or CI logs; distinguish the team's API boundary from upstream UI and downstream persistence owned by other teams

## Working with GCP / BigQuery systems

If the user asks for table schemas, latest rows, or scheduled checks:
- prefer the cloud CLI (`gcloud`, `bq`) for live inspection
- identify project id, dataset, and table exactly
- when checking "latest" rows, confirm what field defines recency before querying
- when a rule is phrased as a threshold, report the denominator, numerator, and percentage
- if a rule is tied to a date boundary, be explicit about the date being checked
- for API-backed schema reconnaissance, confirm the stable field set from repo docs/tests first, then sample only 2-3 live rows for representative values
- if a live survey/API request times out on a broad range, stop widening blindly; fall back to schema docs/tests and only retry a narrower window if live confirmation is still needed

## Dev/prod handling

Always record environment-specific facts separately:
- project IDs
- scheduler names
- table names
- whether prod is synchronized with dev
- any known drift between environments

Pitfall: do not collapse dev and prod into one generic config unless the user explicitly says they are identical.

## Recurring check pattern

For scheduled data-quality checks:
- define the exact source table
- define the recency criterion
- define the threshold
- define the output condition for pass/fail
- keep the message concise and operational

If the user later asks for a scheduled reminder or cron job, reuse the stored rule and keep the prompt self-contained.

## Reference material

- See `references/system-map.md` for a concrete example from a live multi-repo data-platform intake.
- See `references/passport-recog-data.md` for a privacy-sensitive AI passport-recognition API intake, including repo structure, Cloud Run deployment, whole-record accuracy framing, and privacy-preserving evaluation notes.
- See `references/passenger-survey-dashboard.md` for the 意調表 dashboard stack: PM dashboard vs 心情指數 dashboard, Stage 1/2/3 ETL consumers, hosted multi-agent service/web caveats, key routes, BigQuery tables, and denominator pitfalls.
- See `references/passenger-survey-api-schema.md` for the passenger-survey model-labeling API schema recon pattern: `ai-label` vs `label-analyze`, field counts, and the low-token 2-3 row sampling strategy when live endpoints are slow.
- See `references/ai-platform-tool-management.md` for the AI platform / tool-management intake pattern where self-developed tools become the first stable integration boundary.
- See `references/dashboard-source-verification.md` for the dashboard fallback pattern: `/dashboard` can render demo data when the backend hierarchy API fails, so verify the API source and response before concluding data is missing.
