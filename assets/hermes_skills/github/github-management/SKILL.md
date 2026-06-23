---
name: github-management
description: "Use when working across GitHub auth, repos, branches, pull requests, code reviews, issues, and releases."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [GitHub, auth, repositories, pull-requests, issues, reviews, releases]
    related_skills: [hermes-agent]
---

# GitHub Management

## Overview

Use this umbrella for the full GitHub lifecycle: authenticate, clone or create repos, branch safely, open PRs, review changes, triage issues, and manage releases. The goal is to keep one discoverable class-level skill instead of a pile of workflow fragments.

## When to Use

- The user wants to work with GitHub and the exact subtask is not yet clear.
- You need to authenticate first, then perform repo or PR work.
- You need one workflow that spans repo setup, feature branches, PRs, CI, review, and issue tracking.

## Core Workflow

### 1) Authenticate

Prefer `gh` when available; otherwise use `git` + HTTPS token or SSH.
- Confirm whether `gh auth status` succeeds.
- If not, fall back to token-based API calls or SSH keys.
- Set `GITHUB_TOKEN` from the environment or git credentials only when needed.

### 2) Repository operations

- Clone, fork, create, or inspect repos.
- Confirm the actual integration branch before branching; do not assume `main`.
- Keep workspaces isolated per repo when doing multi-repo changes.
- When the user asks to put an existing generated/local project onto a GitHub repo, first check whether it is already a git worktree. If it is not, initialize git, add a `.gitignore` before staging, exclude runtime data/caches/secrets, add a safe `.env.example` when useful, run the project's deterministic tests, then commit/push and verify the remote head.

### 3) PR lifecycle

- Create a topic branch from the correct base.
- Commit with a clear message.
- Push and open the PR.
- Watch CI and fix regressions before merge.
- Merge only after verification.

### 4) Review and triage

- Review local diffs before push.
- Review PRs with comments or formal review decisions.
- Create, label, assign, and close issues with clear triage notes.

## Branch-name suggestions from task context

When the user asks for a recommended task branch name and points to a reminder, issue, or local task folder:

1. Read the referenced task text first, then inspect only enough repo context to identify the project surface area (repo name, current/base branch, relevant package or API contract, and obvious frontend/backend boundary).
2. Prefer one clear branch name in the shape `fix/<area>-<problem>` or `feat/<area>-<capability>`; keep it short, lowercase, hyphenated, and reusable across repos when the same change spans frontend/backend.
3. If the task references a regression or broken display, prefer `fix/…` over `feat/…`.
4. For multi-repo tasks, say whether the same branch name should be used in each repo and confirm the observed base branch; do not create the branch unless asked.
5. Keep the final answer concise: one preferred branch name, a brief reason, and at most one shorter fallback.

### Initializing an existing local project into a new GitHub repo

When the user gives a GitHub URL and asks to commit the current local project:

1. Confirm whether the local directory is already a git worktree with `git rev-parse` / `git status`; if it is not, initialize on `main` unless the user specified another branch.
2. Before `git add .`, create or review `.gitignore` for runtime data and local artifacts. For Python/FastAPI agent repos, usually ignore `__pycache__/`, `.pytest_cache/`, `.env*` except `.env.example`, SQLite runtime DBs, logs, `.DS_Store`, and local maintenance/cache directories copied from dependencies.
3. Add a non-secret `.env.example` with expected environment variable names and safe defaults; do not commit real credentials or ADC files.
4. Run the project’s lightweight verification before the first commit, typically `py_compile` and pytest in fake/offline mode if available.
5. Stage, inspect staged file count/status, run a lightweight secret-pattern grep over staged files, then commit and push with `git push -u origin main` for a fresh remote.
6. Verify the remote head with `git ls-remote --heads origin main` and report branch, commit SHA, tests, and any intentionally ignored runtime files.

## Common Pitfalls

1. **Assuming the base branch is `main`.** Verify the real integration branch first.
2. **Mixing repo setup with PR work.** Authenticate once, then move through the lifecycle.
3. **Reviewing without scope.** Read the diff and changed files before commenting.
4. **Using the wrong skill split.** Repository setup, PR workflow, code review, and issues all belong under one umbrella unless a task needs a highly specialized recipe.
5. **Over-inspecting when the user only asked for naming.** If the user asks for branch-name suggestions, answer with concise branch names first; do not start implementation workflow discovery unless they ask you to implement the change.

## Verification Checklist

- [ ] Auth method identified
- [ ] Repo / branch / remote confirmed
- [ ] PR or issue action matches the user's intent
- [ ] CI or review outcome summarized clearly
- [ ] Any repo-specific conventions captured before changes