# Branch Drift Audit

Use this when the user wants to know how far `main` and `develop`/`development` have diverged without inspecting code diffs.

## Goal
Report only:
- commit distance between branches
- a short outline of the unique commit subjects on each side
- whether the divergence is mostly feature, deploy/config, or merge noise

## Commands

```bash
# Replace branch names as needed
BASE=origin/main
OTHER=origin/develop

git fetch --all --prune

git rev-list --left-right --count "$BASE...$OTHER"
git log --oneline --no-merges "$OTHER..$BASE" -n 20
git log --oneline --no-merges "$BASE..$OTHER" -n 20
```

If the repo uses `development` instead of `develop`, use that remote branch name exactly.

## Notes
- Prefer remote refs (`origin/main`, `origin/develop`) so local unpushed commits do not skew the count.
- Use the left/right counts as the headline result.
- Use commit subjects only for the outline; do not open diffs unless the user asks.
- If a branch only differs by merge commits, say the gap is mostly merge noise.
- For multiple related repos, repeat the same report per repo and summarize the biggest gaps first.
