---
name: react-tsx-safe-editing
description: Safe editing workflow for large React/TypeScript component trees and dashboard UIs, especially when making small UI additions without breaking JSX structure.
---

# React TSX Safe Editing

Use this skill when modifying large React/TypeScript UI files, especially `.tsx` components with nested JSX, complex conditionals, or scrollable dashboard layouts.

## What this skill covers
- Adding a small UI element to a large component without breaking JSX nesting.
- Editing a partial file safely when the target file is too large to reason about from a single excerpt.
- Verifying the edit with diff-focused checks before declaring success.

## Workflow
1. **Locate the exact insertion point.**
   - Read the surrounding block with enough context to see the opening and closing tags/functions.
   - If the file was paginated, re-read the exact area immediately before patching.

2. **Use a narrow anchor.**
   - Prefer replacing a short, unique line or block.
   - Avoid replacing large JSX chunks unless you have the full component in view.

3. **Keep new UI self-contained.**
   - Add new cards, notices, or controls as a closed JSX block.
   - Do not splice raw JSX into the middle of a function body or outside the return tree.

4. **Verify structure after the patch.**
   - Run a diff view and inspect the inserted region.
   - Check for accidental text fragments, duplicate anchors, or malformed JSX.
   - Use `git diff --check` as a fast structural sanity check.

5. **Build or type-check if available.**
   - Prefer the project’s own build/test command after UI edits.
   - If the build is blocked by unrelated environment issues, report that clearly and keep the diff review as the minimum verification.

## Common pitfalls
- **Partial-read patch drift:** a block that looked correct in the excerpt can land in the wrong place if the file changed or the excerpt omitted nearby JSX boundaries.
- **JSX inserted outside `return`:** especially easy when patching around `useEffect`/hook regions and then adding UI later.
- **Overbroad replacement:** large replacements can silently remove important wrappers, styles, or state hooks.

## Practical verification checklist
- The diff shows only the intended UI addition.
- The new JSX sits inside the component’s returned tree.
- No stray `+` markers, placeholder text, or duplicated blocks remain.
- `git diff --check` is clean.

## References
- `references/large-tsx-patching.md` — session note and recovery pattern for safe edits on large dashboard components.
