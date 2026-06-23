# Third-party skill installation notes

This reference captures a real-world pattern for installing an external skill package into Hermes-compatible CLIs.

## Example: FinLab AI skill package

Repository:
- `koreal6803/finlab-ai`

What to look for:
- Repo-level installer script: `https://ai.finlab.finance/install.sh`
- Skill root in the repo tree: `skills/finlab/SKILL.md`
- Supporting files live under `skills/finlab/` (for example `backtesting-reference.md`, `best-practices.md`, `dataframe-reference.md`, `factor-examples.md`, `machine-learning-reference.md`, `trading-reference.md`, `us-market.md`)

How the installer works:
- Detects installed CLIs (`claude`, `codex`, `cursor`, `windsurf`, `gemini`)
- Uses `npx skills add <repo>` when available
- Falls back to cloning the repo and copying `skills/finlab` into the appropriate CLI skill directory
- Installs `uv` only if missing

Useful verification pattern:
- Confirm the repo contains a `skills/<name>/SKILL.md` path before trying to install
- If the skill is not obvious from the homepage, inspect the GitHub tree API or raw README for the install hint
- Prefer verifying the raw `SKILL.md` content before telling the user the skill exists
