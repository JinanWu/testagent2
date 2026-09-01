# Acceptance #18 — Admin-only Full Logs Closure Evidence

Date: 2026-08-14

## Verdict

`IMPLEMENTED` candidate. The fixed source checkpoint passed specification, engineering-quality, and security/privacy review. Ledger closure remains conditional on the checkpoint entering both the Acceptance #18 integration ancestry and `開發中主線`.

## Fixed source checkpoint

- Branch: `Task-A18-Final-Global-Review-Corrections`
- Source checkpoint: `969fbe75624a4cab20970a309fb980bc5f14b612`
- Review range: `e61f57546d648b0be3dc4764314247b6eff88253..969fbe75624a4cab20970a309fb980bc5f14b612`
- Worktree at review: clean

The checkpoint closes the Admin list/detail release boundary, safe list error storage, canonical DTO and projection checks, framework HTTP errors, cross-runtime JSON rules, tombstone-aware UI, production-browser flow, and WAL snapshot consistency.

## Verification evidence

### Backend full suite

```bash
env -u PYTHONPATH AIAGENT_MODEL_MODE=fake \
  /Users/wujinan/.hermes/venvs/testagent2-a07/bin/python -m pytest -q
```

Result: 4,152 collected; 4,148 passed; 4 skipped; 0 failed; exit 0.

### Python 3.11 A18 production scope

A repository-external Python 3.11.15 environment was created under `/tmp` from the dependency ranges in `pyproject.toml`; it did not modify the repository.

```bash
env -u PYTHONPATH AIAGENT_MODEL_MODE=fake \
  /tmp/a18-py311-gate/bin/python -m pytest \
  tests/發布介面/test_CP5_Admin完整紀錄HTTP.py \
  tests/發布介面/test_CP5_Admin完整紀錄E2E.py \
  tests/發布介面/test_A18_ProductionSPA組裝.py -q
```

Result: 67 collected; 67 passed; exit 0.

### Frontend and production browser

```bash
cd ../../multi-agent-web/frontend
npm test -- --run
npm run typecheck
npm run build
A18_BROWSER_PYTHON=/Users/wujinan/.hermes/venvs/testagent2-a07/bin/python \
  npm run browser:smoke
```

Results:

- Vitest: 7 files, 133 tests passed.
- TypeScript application typecheck: exit 0.
- Production Vite build: exit 0.
- Browser TypeScript typecheck: exit 0.
- Production SPA plus canonical ASGI Playwright closure: 1 passed.

The browser fixture uses the production irreversible SQLite redaction service for invocation and run-event nested paths. Assertions cover sanitized DOM rendering and absence from browser storage, URL, console, and page errors.

### Static and schema gates

```bash
env -u PYTHONPATH \
  /Users/wujinan/.hermes/venvs/testagent2-a07/bin/python -m compileall -q 繁中代理
env -u PYTHONPATH \
  /Users/wujinan/.hermes/venvs/testagent2-a07/bin/python scripts/檢查繁中文檔.py
git diff --check
```

Results:

- Python compileall: exit 0.
- Traditional-Chinese checker: 1,501 existing findings, 0 added or changed; exit 0.
- Git whitespace check: exit 0.

The checker result is a baseline ratchet, not a claim that the 1,501 existing findings are resolved.

## Final independent review

All reviewers inspected the same clean source checkpoint `969fbe75624a4cab20970a309fb980bc5f14b612`:

- Specification compliance: `PASS`.
- Engineering quality and deep-module architecture: `APPROVED`.
- Security and privacy: `PASS`; no reproducible blocker.

A prior checkpoint failed because same-type post-construction poisoning of a list child could bypass final domain validation. The final checkpoint adds fresh domain reconstruction at the HTTP release seam, adversarial regression coverage for identifier/status/error-code/time corruption, and matching OpenAPI constraints. The original attack now returns the fixed sanitized JSON 500 response without releasing the injected marker.

## Known non-blocking dependency finding

`npm audit` is not claimed as passing. Review reconfirmed existing findings outside this change range:

- `nanoid 3.3.16`: high severity.
- `postcss 8.5.19`: moderate severity.

These were not introduced by the Acceptance #18 checkpoints and remain separately tracked dependency debt.

## Sensitive-data handling

This evidence intentionally contains no credential, cookie, bearer token, raw invocation payload, redaction source value, or test secret marker. Sensitive values are represented only by their contract-level descriptions.
