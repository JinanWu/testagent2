---
name: autonomous-coding-agents
description: "Use when delegating coding work to external agent CLIs such as Claude Code, Codex, or OpenCode."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [coding-agents, delegation, claude-code, codex, opencode, automation]
    related_skills: [hermes-agent]
---

# Autonomous Coding Agents

## Overview

This umbrella covers running third-party coding agents from Hermes: Claude Code, OpenAI Codex CLI, and OpenCode. Use it when you want another agent to do the implementation, review, or refactor work while Hermes orchestrates scope, safety, and verification.

## When to Use

- The user explicitly asks for Claude Code, Codex, or OpenCode.
- You want a separate coding worker to implement or review a task.
- You need long-running or parallel coding sessions with progress monitoring.
- You need a tight handoff prompt, a single repo/workdir, and a final verification pass.

## Shared Rules

- Keep the prompt specific: repo, branch/workdir, files in scope, and required outcome.
- Prefer one repository or worktree per agent session.
- Use PTY mode when the CLI is interactive.
- Monitor long runs with process polling/logging instead of guessing.
- Verify the agent's self-report by checking git state and running focused tests yourself.

## Agent-Specific Notes

### Claude Code
- Best when you want a high-autonomy coding worker with strong repo-wide editing.
- Requires `claude` auth/setup first.
- Good for feature work, refactors, and PR-oriented tasks.

### Codex CLI
- Best when you want OpenAI's coding CLI in a git repo.
- Use `codex exec` for one-shot tasks, interactive mode for longer sessions.
- Watch for repo scoping and choose the correct model/auth path.

### OpenCode
- Best when you want an open-source coding agent with a TUI or run mode.
- Use `opencode run` for bounded work and interactive sessions for iterative changes.
- Prefer separate workdirs or worktrees for parallel jobs.

## Common Pitfalls

1. **Broad prompts.** External agents do better when the task is tightly scoped.
2. **No verification.** Never trust the agent summary alone; inspect git and run tests.
3. **Shared workdirs.** Parallel coding sessions need isolation.
4. **Interactive tool mismatch.** If the CLI needs a real terminal, use PTY/background handling correctly.

## Verification Checklist

- [ ] Agent choice matches the task
- [ ] Repo/workdir scoped clearly
- [ ] Prompt includes constraints and expected output
- [ ] Changes verified with git diff/status and tests
- [ ] Any long-running session is tracked explicitly