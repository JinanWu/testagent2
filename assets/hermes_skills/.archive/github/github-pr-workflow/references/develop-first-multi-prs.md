# Develop-first multi-PR workflow

Use this when one user request splits cleanly into multiple independent fixes in the same repo.

Rules:
- Branch each fix from `develop`, not `main`.
- Keep one concern per branch and one PR per branch.
- Verify each branch independently before pushing.
- Use a descriptive branch name that matches the isolated fix.

Practical sequence:
1. `git checkout develop && git pull --ff-only origin develop`
2. `git checkout -b fix/<concern>`
3. Make only that fix.
4. Run the focused verification command for that fix.
5. Commit, push, and open a PR with `--base develop`.
6. Return to `develop` and repeat for the next independent fix.

Notes:
- If a branch is accidentally based on `main`, recreate it from `develop` instead of patching around the wrong base.
- Keep PR bodies short: summary, verification, and any special notes about the split.
