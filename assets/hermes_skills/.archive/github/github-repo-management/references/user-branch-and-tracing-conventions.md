# User workspace / branching / tracing conventions

Session-derived conventions for repo work in this environment.

## Workspace
- Default clone location: `/Users/wujinan/Documents/<repo-name>`
- Avoid iCloud Desktop paths unless the user explicitly asks.

## Long-lived branch handling
- When the user says "dev", verify the repository's actual remote branch name first.
- This workspace may use `development` instead of `develop`.
- Always fetch the remote branch before branching:

```bash
git fetch origin development
git switch development
git switch -c feat/<topic>
```

## Chinese commit messages
- For feature branches created from user requests, use Chinese commit messages when the user explicitly asks for a new branch implementation.

## Performance tracing / Sentry pattern
- For Flask dashboards, a minimal Sentry rollout can be:
  - `sentry-sdk` in requirements
  - `FlaskIntegration()` on the backend
  - `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE`
  - BrowserTracing in the template only when `SENTRY_DSN` is present
- Verify with:
  - `python3 -m py_compile ...`
  - a light import/runtime check
  - a template/assertion check for the JS snippet
