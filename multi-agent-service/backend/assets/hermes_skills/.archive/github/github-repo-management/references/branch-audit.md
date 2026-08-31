# Branch Divergence Audit Playbook

Use this when the user wants to know how far `main` and `develop`/`development` have drifted, without inspecting code diffs.

## 1) Identify the repo and branch names

Common patterns:
- `main` vs `develop`
- `main` vs `development`
- compare remote refs, not the currently checked-out local branch

If a repo may use either branch name, inspect:
- `git branch -a --no-color`
- `gh api repos/OWNER/REPO/branches --jq 'map(.name)'

## 2) Count commits on each side

Preferred command:

```bash
git rev-list --left-right --count remotes/origin/main...remotes/origin/develop
```

Interpretation:
- left count = commits unique to `main`
- right count = commits unique to `develop`

If the repo uses `development`, swap the branch name accordingly.

## 3) Summarize the shape of the gap

For a quick outline only:

```bash
git log --oneline --no-merges remotes/origin/develop..remotes/origin/main -n 20
git log --oneline --no-merges remotes/origin/main..remotes/origin/develop -n 20
```

Use the subject lines to bucket changes into:
- deploy/config only
- resource tuning
- data schema / API contract changes
- business logic changes
- merge commits only

## 4) For org-wide audits, list candidate repos first

```bash
gh repo list ORG --limit 300 --json name,description,defaultBranchRef,url
```

Then filter by repo name/description keywords and run the branch-count check only on the relevant repos.

## 5) Pitfalls

- `gh compare` / `gh api compare` direction matters: `A...B` means commits unique to B are counted as `ahead_by` in the response depending on order. Always sanity-check with `git rev-list --left-right --count`.
- A repo may have a local `develop` checked out while the real remote branch is `development`.
- Merge commits can make the gap look larger than the number of functional changes; use `--no-merges` when the user only wants the substantive outline.
- If the user only wants the difference size, do not fetch file diffs; commit counts plus commit subjects are enough.
