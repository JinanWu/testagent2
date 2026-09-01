# Pytest in nested repos / monorepos

When a repository lives inside a larger workspace, `pytest` may accidentally collect tests from sibling projects or fail to import the target package because the repo root is not on `sys.path`.

Reliable pattern:
1. Run pytest with the target repo as the working directory and pin discovery with `--rootdir=.` if needed.
2. Add `tests/conftest.py` that inserts the repo root (usually `Path(__file__).resolve().parents[1]`) into `sys.path` before imports.
3. Re-run only the target test file first, then the broader suite if discovery is clean.

Signals this applies:
- `ModuleNotFoundError` for the package under test during collection.
- Pytest unexpectedly collecting tests from unrelated sibling repos.
- The code imports fine in an interactive shell but not under pytest.

Notes:
- Keep this as a test-discovery/import fix, not a general environment rule.
- Prefer the smallest change that makes test discovery local to the repo.
