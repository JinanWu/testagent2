# Cloud Run cache-stampede OOM in sync FastAPI dashboards

Use this when a Cloud Run service returns 503 while the app is mostly healthy, especially for dashboard APIs that lazily build an in-memory tree/cache.

## Failure shape

- Cloud Run service/revision is `Ready=True`, but request logs show bursts of 503.
- 503 request logs may say malformed response / connection error, while nearby platform logs say:
  - `Memory limit of <N> MiB exceeded with <M> MiB used`
  - `Container terminated on signal 9`
  - new instance starts shortly after.
- 503s cluster by endpoint and by second, e.g. many `/dashboard/api/.../trend` calls in the same second.
- App logs may show normal 200s before/after the burst, so do not diagnose as a global routing failure.

## Root-cause pattern

A small BigQuery snapshot can still cause OOM if cold-cache requests stampede:

1. Frontend fans out many child trend/detail requests at once.
2. Each backend request sees module-level cache as empty.
3. The backend builds a full hierarchy/cache per request instead of once.
4. Python object expansion and transient structures make one build hundreds of MB even if JSON output is only a few MB.
5. Cloud Run `containerConcurrency` lets many threads share one memory limit; `maxScale=1` concentrates all traffic on one instance.
6. Several full builds overlap and exceed memory, causing SIGKILL and 503.

This is different from “the data is simply too large.” Quantify both raw data and process memory before making that claim.

## Evidence to collect

- Cloud Run config: memory, CPU, maxScale, containerConcurrency, latest revision.
- Request-log counts:
  - 5xx count by endpoint/path.
  - 5xx count by second/minute to reveal bursts.
  - memory-limit and signal-9 logs in the same window.
- Code path:
  - endpoint sync/async mode.
  - whether sync FastAPI endpoints run in threadpool.
  - cache miss path and whether multiple requests can build the same cache concurrently.
  - frontend fanout/preload loops.
- Data/memory quantification:
  - raw snapshot JSON bytes, node counts, embedded-row counts.
  - local or staging run measuring RSS before/after the cold build and cached call latency.

## Fix pattern: singleflight around cache build

For sync FastAPI endpoints (`def`, not `async def`), use `threading.RLock`/`Lock`, not `asyncio.Lock`, because FastAPI runs sync endpoints in a threadpool.

Recommended shape:

```python
from threading import RLock

_cache = None
_initial_cache = None
_cache_lock = RLock()


def load_hierarchy(force_reload: bool = False):
    global _cache, _initial_cache
    if _cache is not None and not force_reload:
        return _cache

    with _cache_lock:
        if force_reload:
            _cache = None
            _initial_cache = None
        elif _cache is not None:
            return _cache

        result = build_expensive_hierarchy()
        _cache = result
        _initial_cache = None
        return _cache
```

Notes:

- Use double-checked locking so hot-cache requests avoid lock overhead.
- Put `force_reload` invalidation inside the same lock as cache build.
- Use one lock for related initial/full caches if building one invalidates the other.
- Do not cache failed/partial results; assign only after the expensive build succeeds.
- Keep the lock boundary around cache check/build/assign. Avoid wrapping higher-level endpoint functions unless necessary.

## Regression tests

Add tests that monkeypatch the expensive builder and call the cache loader from many threads using `ThreadPoolExecutor` + `Barrier`. Assert:

- concurrent cold calls invoke the builder exactly once;
- all callers receive the same cached object;
- `force_reload=True` replaces the cached object and increments the builder count once.

## Follow-up hardening

Singleflight is a low-risk stopgap. For full remediation, also consider:

- replacing full hierarchy loads in single-node trend endpoints with direct node/path trend queries;
- limiting frontend fanout/preload concurrency or loading only visible/current chart data;
- lowering Cloud Run concurrency or increasing memory as temporary mitigation;
- adding request/build timing and memory observability around cache misses.
