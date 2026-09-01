# Develop-first docs refresh pitfalls

Use this when the repo family’s working branch is `develop` and the task is a docs/README refresh.

Checklist:
1. Confirm the checkout is on `develop` before creating the docs branch.
2. Create the docs branch from `develop`, not from `main`.
3. Inspect the real entrypoint/config files before rewriting the README.
4. Commit and push one branch per repo.
5. Create the PR with `--base develop`.
6. Verify the PR base before asking for review.
7. If you accidentally created the branch or PR from `main`, close/delete the bad PR and recreate it from `develop`.

Common pitfall:
- Fixing the base on an already-wrong docs PR can leave extra cleanup work and confusion. For this repo family, recreate from `develop` when in doubt.
