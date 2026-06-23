---
name: ai-lab-news-daily-writing
description: Use when writing or reviewing AI Lab News daily drafts for the company IT or information department.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [ai-news, daily, writing, newsletter]
    related_skills: [ai-news-workflow, ai-news-editorial-guardrails]
---

# AI Lab News Daily Writing

## Overview

AI Lab News is a daily AI update for the company's IT / information department. It should be concise, high-signal, and source-backed.

## When to Use

Use this skill when:

- Producing an AI Lab News daily draft.
- Reviewing an AI Lab News draft.
- Converting a shortlist into a daily issue.

## Audience

Primary audience: IT / information department colleagues.

They care about:

- AI product and API changes.
- Coding-agent and Copilot/Codex/Claude Code changes.
- Tool updates.
- Risk, deprecation, privacy, security, or breaking changes.

## Template

Canonical template path:

`/Users/wujinan/Documents/AI-News/templates/ai-lab-news-daily-template.md`

Recommended subtitle:

`每日重點、工具更新、技術觀察、風險提醒`

Preferred editorial shape for this user:

- First, brief the user on source groups and candidate themes.
- Then wait for the user to choose the angle before writing the issue.
- Aim for about a 5-minute read.
- Prefer roughly 4 main items.
- Use a magazine-like article structure with a clear thesis, not a raw digest.

Sections:

1. 今日一句話摘要
2. 重要更新
3. 技術觀察
4. 風險提醒
5. 來源連結

Support reference for the "busy-reader publication" pattern:

`references/busy-reader-publication-pattern.md`

Support reference for fixing outline/digest drafts into articles:

`references/publication-not-digest.md`

## Writing Rules

- Include 3-5 main items only.
- Prefer official sources and changelogs.
- Write `publication.md` as a publishable, opinionated report, not a directory, outline, or bullet digest. It should have a clear editorial thesis, descriptive context, reasoning, and implications for the IT / information department.
- When the user says the draft feels like a "目錄", "索引", or "一堆條列", rewrite it into a magazine-style article: strong headline, one-sentence takeaway, then 3 body sections with transitions and a closing conclusion.
- Aim for a readable length of roughly 5 minutes for internal readers unless the user explicitly asks for shorter.
- Use bullets sparingly inside the article; they may support the argument but should not replace narrative explanation.
- Explain why each item matters to technical colleagues.
- Keep `重要更新` easy to understand: lead with the work impact in plain Chinese before naming products or technical terms. Avoid dense strings such as CLI / SDK / sandbox / MCP / agent logic in the body; if a term is necessary, explain it immediately with a simple analogy (for example, sandbox = 安全的試做空間) and move product-name detail into source lines.
- If the user says a section has too many professional terms or is too complex, rewrite the section in simpler language rather than merely shortening it. Preserve source links, but translate the signal into concrete examples: 文件整理、資料比對、初稿、例行檢查、待辦追蹤、權限與驗收.
- For recurring explanatory columns inside AI Lab News, write them as natural publication columns, not course notes. If the user provides reference text and asks for “簡單的方式講多一點大約一千字,” expand to roughly 1,000 Chinese characters with a conversational opening, clear diagnostic sequence, concrete examples, and a practical closing checklist.
- Include source URL for every item.
- Mark uncertainty explicitly.
- Do not include a `導入可能性` field.
- If the user says the piece is not a刊物, the corrective action is to add narration and reasoning, not just to rearrange the same bullets.

## Review Checklist

- [ ] 3-5 items.
- [ ] Every item has a source.
- [ ] Suitable for IT/information department readers.
- [ ] Includes technical observation or risk note.
- [ ] No `導入可能性` field.
