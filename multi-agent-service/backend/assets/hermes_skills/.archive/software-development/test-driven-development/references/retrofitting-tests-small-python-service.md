# Retrofitting focused tests in a small Python service

Use this when a Python service has little/no existing test harness but a bug fix needs TDD coverage.

## Pattern

1. Add a minimal `tests/` package or test file using stdlib `unittest` if no project test framework is established.
2. Prefer behavior tests around the public service method rather than live API calls.
3. Use small fake collaborators assigned through `__dict__` or dependency seams to avoid calling external services during tests.
4. If a module requires environment variables at import time, set deterministic test defaults before importing the module under test:
   ```python
   import os
   os.environ.setdefault("GEMINI_MAX_WORKERS", "4")
   ```
5. Verify RED with the focused test before production changes.
6. Verify GREEN with the focused test plus the available suite, e.g.:
   ```bash
   python3 -m unittest tests.test_service.TestCaseName.test_behavior -v
   python3 -m pytest tests -q
   python3 -m compileall app.py src tests
   ```

## Example bug class: retry-on-empty-result

For services that parse external AI/API responses, test these separately:
- first empty parsed result triggers exactly one retry;
- retry success returns the second parsed result;
- retry still empty does not loop indefinitely and returns the final result.

Keep the test independent from credentials, network, cloud SDK state, and real image/API payloads.