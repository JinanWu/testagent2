# Skill installation and verification notes

Use this when a skill comes from an external repo rather than the built-in skill library.

## Install pattern

- Mirror the upstream skill directory into Codex's skills directory:
  - `~/.codex/skills/<skill-name>/`
- Keep the upstream `SKILL.md` plus any linked `references/`, `templates/`, or `scripts/` files together.
- If the upstream repo exposes an installer, prefer it only if it is safe to run in the current environment.

## Verification pattern

1. Create a temporary git repository.
2. Run a short `codex exec` prompt from inside that repo.
3. Ask Codex to read the installed skill and answer with one or two facts from it.
4. If Codex cites the expected skill content, the install is usable.

Example verification prompt:

- “Using the <skill> skill, answer with the exact install command and one prerequisite.”

This is a good smoke test because Codex requires a git repository and will surface whether the skill was actually loaded.