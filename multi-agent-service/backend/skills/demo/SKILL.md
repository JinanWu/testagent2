---
name: demo
description: Use when validating a published structured API endpoint.
---

# Demo Published API Skill

Use this skill to create a small structured endpoint for deployment and runtime acceptance.

## Behaviour

1. Accept input that conforms to the published input schema.
2. Return concise JSON that conforms exactly to the published response schema.
3. Preserve an explicit marker field when the caller supplies one.
4. Do not invoke tools unless the published endpoint explicitly authorizes them.
