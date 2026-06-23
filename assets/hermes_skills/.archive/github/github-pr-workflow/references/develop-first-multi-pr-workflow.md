# Develop-first multi-PR workflow

Use this when a single request contains multiple unrelated fixes in a repo family that branches from `develop`.

## Checklist

1. Confirm the integration branch is `develop`.
2. Create one branch per fix, each from `develop`.
3. Keep each branch scoped to a single change.
4. Run the repo build/test command for each branch.
5. Commit with a focused message.
6. Push each branch and open one PR per branch.
7. Base every PR on `develop`.

## Practical pattern

- Fix A → branch A → PR A
- Fix B → branch B → PR B

Do not combine unrelated UI fixes into one branch unless the user explicitly asks for a bundled change.

## Verification

- Prefer the lightest build/test command that covers the changed area.
- If the repo has a known `build` script, run it before opening the PR.
- Mention the verification command in the PR body.
