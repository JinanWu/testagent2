---
name: codex
description: "Delegate coding to OpenAI Codex CLI (features, PRs)."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Coding-Agent, Codex, OpenAI, Code-Review, Refactoring]
    related_skills: [claude-code, hermes-agent]
---

# Codex CLI

Delegate coding tasks to [Codex](https://github.com/openai/codex) via the Hermes terminal. Codex is OpenAI's autonomous coding agent CLI.

## When to use

- Building features
- Refactoring
- PR reviews
- Batch issue fixing
- Front-end presentation sites / browser-openable decks

Requires the codex CLI and a git repository.

## Front-end presentation builds

When the user wants a presentation site or deck rendered in HTML/front-end form, give Codex a tight brief: target repo, audience, narrative constraints, page-by-page structure, visual direction, one central editable data file, and explicit responsive requirements. Keep sensitive comparisons neutral (e.g. `現行方案 / 新方案`) and ask for README + local run instructions.

For this class of work, also tell Codex to:
- treat empty subtitles/captions as optional so blank copy does not reserve vertical space
- verify the deck at short-height and narrow-width breakpoints, not just a desktop 16:9 canvas
- allow vertical scrolling or adaptive scaling when viewport height is insufficient, instead of hard-clipping at 100vh
- check slide titles, subtitles, charts, nav, and page counters for overflow on each slide

See `references/presentation-site-prompt.md` for a reusable prompt pattern, and `references/presentation-deck-rwd-checklist.md` for the responsive/legibility checklist that emerged from this session.

## Pre-flight scope

- If the user is only asking whether Hermes can drive Codex, answer the capability question directly and wait for an actual repo/task before launching Codex.
- Before starting Codex, make sure the request is specific enough to describe in one prompt: target repo, desired change, and any constraints.
- Prefer a tight prompt over a broad "improve this codebase" request.
- See `references/codex-preflight.md` for a compact checklist.
- See `references/log-only-instrumentation-prompt-guardrails.md` before delegating logging-only instrumentation; it captures guardrails to avoid Codex turning log requests into signature/control-flow rewrites.

## Prerequisites

- Codex installed: `npm install -g @openai/codex`
- OpenAI auth configured: either `OPENAI_API_KEY` or Codex OAuth credentials
  from the Codex CLI login flow
- **Must run inside a git repository** — Codex refuses to run outside one
- Use `pty=true` in terminal calls — Codex is an interactive terminal app
- For external skill packs, keep the skill directory under `~/.codex/skills/<name>/` and see `references/skill-installation-and-verification.md` for a reliable smoke-test pattern.

For Hermes itself, `model.provider: openai-codex` uses Hermes-managed Codex
OAuth from `~/.hermes/auth.json` after `hermes auth add openai-codex`. For the
standalone Codex CLI, a valid CLI OAuth session may live under
`~/.codex/auth.json`; do not treat a missing `OPENAI_API_KEY` alone as proof
that Codex auth is missing.

## One-Shot Tasks

```
terminal(command="codex exec 'Add dark mode toggle to settings'", workdir="~/project", pty=true)
```

For scratch work (Codex needs a git repo):
```
terminal(command="cd $(mktemp -d) && git init && codex exec 'Build a snake game in Python'", pty=true)
```

## Background Mode (Long Tasks)

```
# Start in background with PTY
terminal(command="codex exec --full-auto 'Refactor the auth module'", workdir="~/project", background=true, pty=true)
# Returns session_id

# Monitor progress
process(action="poll", session_id="<id>")
process(action="log", session_id="<id>")

# Send input if Codex asks a question
process(action="submit", session_id="<id>", data="yes")

# Kill if needed
process(action="kill", session_id="<id>")
```

## Key Flags

| Flag | Effect |
|------|--------|
| `exec "prompt"` | One-shot execution, exits when done |
| `--full-auto` | Sandboxed but auto-approves file changes in workspace |
| `--yolo` | No sandbox, no approvals (fastest, most dangerous) |

## PR Reviews

Clone to a temp directory for safe review:

```
terminal(command="REVIEW=$(mktemp -d) && git clone https://github.com/user/repo.git $REVIEW && cd $REVIEW && gh pr checkout 42 && codex review --base origin/main", pty=true)
```

## Parallel Issue Fixing with Worktrees

```
# Create worktrees
terminal(command="git worktree add -b fix/issue-78 /tmp/issue-78 main", workdir="~/project")
terminal(command="git worktree add -b fix/issue-99 /tmp/issue-99 main", workdir="~/project")

# Launch Codex in each
terminal(command="codex --yolo exec 'Fix issue #78: <description>. Commit when done.'", workdir="/tmp/issue-78", background=true, pty=true)
terminal(command="codex --yolo exec 'Fix issue #99: <description>. Commit when done.'", workdir="/tmp/issue-99", background=true, pty=true)

# Monitor
process(action="list")

# After completion, push and create PRs
terminal(command="cd /tmp/issue-78 && git push -u origin fix/issue-78")
terminal(command="gh pr create --repo user/repo --head fix/issue-78 --title 'fix: ...' --body '...'")

# Cleanup
terminal(command="git worktree remove /tmp/issue-78", workdir="~/project")
```

## Batch PR Reviews

```
# Fetch all PR refs
terminal(command="git fetch origin '+refs/pull/*/head:refs/remotes/origin/pr/*'", workdir="~/project")

# Review multiple PRs in parallel
terminal(command="codex exec 'Review PR #86. git diff origin/main...origin/pr/86'", workdir="~/project", background=true, pty=true)
terminal(command="codex exec 'Review PR #87. git diff origin/main...origin/pr/87'", workdir="~/project", background=true, pty=true)

# Post results
terminal(command="gh pr comment 86 --body '<review>'", workdir="~/project")
```

## Rules

1. **Always use `pty=true`** — Codex is an interactive terminal app and hangs without a PTY
2. **Git repo required** — Codex won't run outside a git directory. Use `mktemp -d && git init` for scratch
3. **Use `exec` for one-shots** — `codex exec "prompt"` runs and exits cleanly
4. **`--full-auto` for building** — auto-approves changes within the sandbox
5. **Background for long tasks** — use `background=true` and monitor with `process` tool
6. **Don't interfere** — monitor with `poll`/`log`, be patient with long-running tasks
7. **Parallel is fine** — run multiple Codex processes at once for batch work
8. **Prompt guardrails for repo changes** — tell Codex the exact branch/workdir, whether to commit/push, what files or untracked directories must not be touched, schema/API compatibility constraints, and the required final summary shape.
9. **Verify Codex output yourself** — after Codex exits, run `git status --short --branch`, inspect `git diff --stat`/targeted diffs, and execute the focused test command. Do not rely only on Codex's final self-report for changed files or test results.
10. **For log-only / instrumentation requests, hard-constrain Codex to minimal diffs** — explicitly forbid function signature changes, control-flow rewrites, retry/timeout/concurrency changes, API schema changes, and large try/except restructures unless the user asks for behavior changes. After Codex exits, grep the diff for changed `def`/`async def` lines and inspect service/retry code paths; if it over-instruments, revert to the pre-task version and reapply only logger statements plus any small request-context helpers needed for logging.
