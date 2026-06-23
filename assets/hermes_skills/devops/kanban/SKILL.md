---
name: kanban
description: "Use when routing, planning, or executing Hermes Kanban work across orchestrator and worker roles."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [kanban, orchestration, workers, routing, durable-work]
    related_skills: [hermes-agent]
---

# Kanban

## Overview

Use this umbrella when work should live on the Hermes Kanban board instead of staying inside a single turn. It covers both orchestration and worker execution, dependency graphs, handoffs, and blocked/retry flows.

## When to Use

- The task should survive a restart or continue asynchronously.
- You need to split work across specialists or profiles.
- The user wants durable tracking, handoff comments, or blocked states.
- You need to route work rather than execute it yourself.

## Core Workflow

### Orchestrator view
- Break the request into concrete lanes.
- Map each lane to a real profile.
- Create independent tasks in parallel.
- Add parents only when one task truly depends on another.
- Summarize the graph back to the user.

### Worker view
- Inspect the task and the thread first.
- Work inside the assigned workspace only.
- Block when a human decision is needed.
- Complete only when the task is actually done.
- Send concise heartbeats when the work is long-running.

## Common Pitfalls

1. **Inventing dependencies.** Link tasks only when one lane truly waits on another.
2. **Creating one giant task.** Split independent lanes.
3. **Completing too early.** Block if human review or input is still needed.
4. **Using the wrong profile.** Assign work to an actual available profile.

## Verification Checklist

- [ ] Task graph matches the user's request
- [ ] Parent/child links are real dependencies
- [ ] Worker handoff text is self-contained
- [ ] Block/complete state reflects the actual finish line
- [ ] The live board state was checked before action