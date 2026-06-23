---
name: ai-news-editorial-guardrails
description: Use when filtering, deduplicating, grading, or quality-checking AI Lab News and AI智報 items.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [ai-news, editorial, review, quality]
    related_skills: [ai-news-workflow, ai-news-source-recon]
---

# AI News Editorial Guardrails

## Overview

This skill defines filtering, deduplication, grading, and review rules for AI Lab News and AI智報.

## When to Use

Use this skill when:

- Creating a shortlist.
- Turning raw items into draft candidates.
- Checking whether a claim is safe to include.
- Reviewing draft quality before handing it to the user.

## Initial Filter

Ask these questions for every item:

1. Is the source credible?
2. Is it related to AI applications, AI coding, models, tools, research, or work productivity?
3. Does it matter to the target audience?
4. Does it contain an actual change, risk, usable method, or clear insight?

Statuses:

- keep: eligible for shortlist.
- watch: store for later but do not draft.
- reject: exclude.

## Deduplication

When several sources cover the same event, prefer:

1. First-party official source.
2. Original changelog/release note.
3. Engineering/research article with implementation detail.
4. High-quality supplementary article.

Media and social discussions can be added as context but should not replace first-party sources.

## Grading

### A Grade

Daily candidate. Examples: official changelog, model/API update, breaking change, security/privacy/deprecation, important tool release.

### B Grade

Weekly candidate. Examples: deep technical article, research paper, benchmark update, tutorial, practical workflow.

### C Grade

Observation only. Examples: noisy hype, unverified community discussion, incomplete launch info.

## Draft Rules

- Always cite sources.
- Mark uncertain details explicitly.
- Do not overstate impact.
- Avoid hype language.
- Do not include a `導入可能性` field.
- Keep daily drafts concise.
- Make weekly drafts understandable to non-technical colleagues.

## Verification Checklist

- [ ] Every included item has a source URL.
- [ ] No claim depends only on noisy community/media sources.
- [ ] Uncertainty is labeled.
- [ ] Duplicates are collapsed.
- [ ] No `導入可能性` field appears.
