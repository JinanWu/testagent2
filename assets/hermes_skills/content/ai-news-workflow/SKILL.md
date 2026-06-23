---
name: ai-news-workflow
description: "Use when operating the AI Lab News and AI智報 newsletter workflow, including storage layout, roles, source-to-draft pipeline, and review handoff."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [ai-news, newsletter, workflow, editorial]
    related_skills: [ai-news-source-recon, ai-news-editorial-guardrails]
---

# AI News Workflow

## Overview

This skill defines the durable workflow for two publications:

- AI Lab News: daily AI update for the company's IT / information department.
- AI智報: weekly AI application digest for other departments.

Hermes acts as collector, editorial assistant, and gatekeeper. The user is the final reviewer and approver.

## When to Use

Use this skill when:

- Creating, updating, or running the AI Lab News / AI智報 process.
- Producing daily or weekly drafts.
- Reading or writing files under `/Users/wujinan/Documents/AI-News/`.
- Explaining the newsletter production pipeline.

Do not use this skill for unrelated content writing.

## Canonical Storage Root

Use this root unless the user explicitly changes it:

`/Users/wujinan/Documents/AI-News/`

Important files:

- `sources/source_registry.json` — machine-readable source registry.
- `sources/source_registry.md` — human-readable source table.
- `templates/ai-lab-news-daily-template.md` — daily template.
- `templates/ai-zhibao-weekly-template.md` — weekly template.
- `templates/review-checklist.md` — review checklist.
- `data/raw/YYYY-MM-DD.jsonl` — raw collected items.
- `data/normalized/YYYY-MM-DD.jsonl` — normalized items.
- `data/shortlist/YYYY-MM-DD.md` — filtered shortlist.
- `AI Lab News/YYYY-MM-DD/publication.md` — review-ready publication candidate, written as if publishable but not yet approved.
- `AI Lab News/YYYY-MM-DD/sources.md` — sources actually used in the publication plus excluded/downgraded candidates.
- `AI Lab News/YYYY-MM-DD/diff_check.md` — comparison with the previous issue and recent-duplicate check.
- `AI Lab News/YYYY-MM-DD/review_package.md` — index for reviewer handoff.
- `AI Lab News/YYYY-MM-DD/draft.md` — optional internal working draft; do not treat this as the user-facing review artifact.
- `AI智報/YYYY-Www/draft.md` — weekly draft.

## Workflow

1. Collect from fixed sources.
2. Store raw items before writing any prose.
3. Normalize fields and preserve canonical URLs.
4. Filter, dedupe, and grade items.
5. Interpret broad "ainews" requests as covering both publication tracks unless the user explicitly narrows the scope:
   - AI Lab News: create the daily review package for the current date when enough material exists.
   - AI智報: create the weekly source briefing for the current ISO week, and only draft after the user chooses an angle.
   - If only one track is produced, state that explicitly in the handoff so the omission is visible.
6. Produce a source briefing first, not the final issue:
   - source groups
   - candidate themes / angles
   - a recommended editorial thesis
   - any obvious exclusions or redundancies
7. If the user asks to start next week's work but has not yet chosen a direction, stop the weekly AI智報 at the briefing stage and present concise numbered angle options. Do not advance to weekly publication drafting.
8. For AI Lab News daily issues, after collection and theme selection, produce a review package without waiting for a separate angle confirmation unless the user explicitly asks for briefing-only mode:
   - `publication.md`: review-ready publication candidate, with no editor-only notes in the main body.
   - `sources.md`: sources actually used, plus excluded/downgraded candidates.
   - `diff_check.md`: compare against the previous `publication.md` and flag recent duplicates; if no previous issue exists, say so explicitly.
   - `review_package.md`: reviewer-facing index and checklist.
9. Wait for user review before treating anything as final.

See `references/briefing-first-process.md` for the preferred AI Lab News handoff format.

## Publication Rules

### Editorial Style for This User

- When the user asks to revise a newsletter or explain a change, prefer a plain-language draft in chat first if they explicitly ask to preview it before editing files.
- Keep wording simple, concrete, and easy to scan; avoid over-polishing into dense or overly abstract prose unless the user asks for a more literary or technical style.
- If the user says the text is too verbose or hard to understand, simplify the next draft instead of defending the current wording.
- For AI Lab News, aim for clear editorial phrasing that a busy reader can grasp quickly: one idea per sentence when possible, minimal jargon, and concrete analogies only when they genuinely clarify.

### AI Lab News

- Audience: IT / information department.
- Cadence: daily.
- Length: 3-5 key items.
- Focus: latest changes, tools, API/model updates, technical observations, risk notes.
- When the user asks for a "刊物" or "試產" version, use a publication-style narrative rather than a source digest; see the busy-reader publication pattern reference in `ai-lab-news-daily-writing`.
- If the user says the output feels like an outline,目录, or source list, do not keep the same structure and add a few adjectives; rewrite the piece into a thesis-driven article with transitions and a conclusion.

### AI智報

- Audience: other departments.
- Cadence: weekly.
- Focus: plain-language summary, useful tools, workplace cases, small tips, reusable prompts.
- Derive from daily materials; do not restart from scratch unless requested.
- If the user asks to plan the coming week before the daily materials are finalized, create a briefing-level proposal first and stop there until they choose the angle.

## Hard Rules

- Never publish automatically.
- Always preserve source links.
- Do not include a `導入可能性` field.
- Social/media sources may supplement but should not be the sole authority for a claim.
- Mark uncertain information explicitly.

## Verification Checklist

- [ ] Output stayed under `/Users/wujinan/Documents/AI-News/`.
- [ ] Source links are preserved.
- [ ] Draft is clearly not final.
- [ ] No `導入可能性` field appears.
