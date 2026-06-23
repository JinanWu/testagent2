---
name: ai-news-source-recon
description: Use when collecting, evaluating, or maintaining source feeds for AI Lab News and AI智報.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [ai-news, sources, rss, api, scraping]
    related_skills: [ai-news-workflow, ai-news-editorial-guardrails]
---

# AI News Source Recon

## Overview

This skill governs how to collect and evaluate AI news sources for the AI Lab News / AI智報 workflow.

Source registry path:

`/Users/wujinan/Documents/AI-News/sources/source_registry.json`

## When to Use

Use this skill when:

- Adding or evaluating sources.
- Collecting daily raw items.
- Checking RSS/API/HTML source health.
- Deciding whether a source belongs in main, auxiliary, observation, or monitoring pools.

## Source Priority

- A: Main source. Official, high-signal, suitable for daily or core weekly use.
- B: Auxiliary source. Useful for weekly context, cases, tools, or local market view.
- C: Observation source. Useful for trends/pain points, but noisy; not a main citation.
- D: Monitoring source. Webhook/API infrastructure source, not necessarily editorial content.

## Fetch Methods

Prefer in this order:

1. RSS or official API.
2. GitHub Releases API for repositories.
3. HTML diff for official changelog pages.
4. HTML parser for official blogs without feeds.
5. Webhook only after the initial file-based workflow is stable.

## Required Fields

Each normalized item should include:

- id
- fetched_at
- source_id
- source_name
- source_type
- fetch_method
- title
- published_at
- canonical_url
- summary
- category
- reliability
- relevance_score
- priority_level
- status
- notes

## Collection Rules

- Read the source registry before collecting.
- Do not invent new sources during a scheduled run.
- Preserve the canonical URL.
- Keep raw data before summarizing.
- Write source failures to `data/index/source_health.jsonl`.
- Make collection safe to rerun; use URL/title dedupe.

## High-Signal Source Families

Main sources include OpenAI, Anthropic/Claude, Google/Gemini, GitHub Copilot, Hugging Face, arXiv/OpenReview, SWE-bench/LiveCodeBench, and key coding-agent repos.

Observation sources include Reddit, Stack Overflow, TechCrunch, VentureBeat, and Towards Data Science.

## Common Pitfalls

1. Treating media rewrites as first-party sources.
2. Letting Product Hunt or Reddit dominate the draft.
3. Grabbing too many papers without topic filters.
4. Failing the whole run because one source is temporarily unavailable.
5. Treating registry template sources as fetchable endpoints. Example: `github_releases_api` is a generic template; skip it during runs and fetch concrete repo sources such as OpenHands, aider, Cline, and Continue instead.
6. Assuming `html_parser_or_rss` means RSS is available. If no `rss_url` is present, try HTML parsing first or fall back from RSS/XML parse errors to HTML extraction.
7. Using arXiv over plain HTTP or with overly complex query strings. Prefer `https://export.arxiv.org/api/query` with URL-encoded focused queries and retry with a simpler query if XML parsing fails.

## Verification Checklist

- [ ] Registry was read.
- [ ] Raw items were saved.
- [ ] Source failures were logged, not fatal.
- [ ] Duplicates were not repeatedly appended.
