# Multi-repo documentation refresh checklist

Use this when updating READMEs or other docs across a repo family.

Checklist:
1. Identify the project’s integration branch before editing. Do not assume `main`; many projects use `develop` for docs and day-to-day changes.
2. Inspect the README against the real entrypoint/config files first.
3. Create one branch per repo.
4. Keep each repo isolated in its own local checkout/workspace.
5. Commit and push per repo.
6. Open one PR per repo with the base branch set to the project’s integration branch.
7. Verify the branch base before asking for review.

Pitfall seen in practice:
- If the repo family uses `develop` as the working branch, creating docs branches from `main` can produce misleading PRs and extra cleanup work. Fix by rebasing or recreating the branch against `develop` before opening the PR.
