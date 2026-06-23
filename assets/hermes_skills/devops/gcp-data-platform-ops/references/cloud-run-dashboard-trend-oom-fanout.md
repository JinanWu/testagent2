# Cloud Run dashboard trend 503 from frontend fan-out + full-tree rebuild

Use this reference when a dashboard Cloud Run service intermittently returns 503 for trend/detail endpoints while the service revision is Ready.

## Failure shape

- Cloud Run service/revision is Ready and some endpoints still return 200.
- 503s cluster around dashboard trend/detail endpoints, often `/dashboard/api/v1/satisfaction/trend` and leaf/detail endpoints such as `/tours/...`.
- Logs show `Memory limit of <N> MiB exceeded`, `Container terminated on signal 9`, and `The request failed because either the HTTP response was malformed or connection to the instance had an error`.
- Latencies often plateau around the heavy backend rebuild duration before the 503s complete.
- A new instance starts after the OOM, which clears in-memory caches and can cause the next burst to repeat the problem.

## Investigation pattern

1. Verify live Cloud Run state first:
   - service/revision Ready
   - memory/cpu/concurrency/maxScale
   - latest image/revision
2. Query Cloud Logging for the incident window:
   - `httpRequest.status=503`
   - text payload containing `Memory limit`, `signal 9`, `Starting new instance`
   - group by endpoint/revision and compare timing.
3. Inspect frontend call pattern:
   - Look for `forEach`, `Promise.all`, `map(...fetch...)`, or eager prefetch of child trend/detail APIs.
   - Confirm whether a single navigation/click triggers requests for every child node, not just the currently rendered chart/detail.
4. Inspect backend trend/detail path:
   - Check whether a small endpoint calls a full hierarchy/materialization function.
   - Look for cold-cache paths that read all historical snapshots, recursively build the whole tree, compute trend for every node, or backfill detail text for all leaves.
5. Quantify data shape cheaply:
   - count snapshots/periods
   - min/max period
   - average/max `LENGTH(TO_JSON_STRING(metrics_tree))`
   - do not download the full trees.

## Root-cause pattern

A common root cause is:

frontend eager child trend prefetch
→ many concurrent `/trend` calls in one navigation
→ each backend request sees an empty cache and independently builds the full dashboard hierarchy/history
→ multiple large trees coexist in one Cloud Run instance
→ memory exceeds the configured limit
→ Cloud Run SIGKILLs the container
→ clients receive 503 malformed response / connection error.

This is especially likely with Cloud Run settings such as low memory, `maxScale=1`, and high `containerConcurrency` because all burst traffic is forced into one process.

## Fix order

1. **Immediate frontend stopgap:** remove or throttle eager child trend/detail prefetch. Load only the currently visible chart/detail; optionally fetch child trends lazily on hover/expand or with a small concurrency limit.
2. **Backend singleflight:** protect cold full-hierarchy cache builds with a process-level lock so concurrent requests share one build instead of multiplying memory.
3. **Endpoint-specific backend path:** make `/trend` compute only the requested node from historical snapshots instead of materializing the full API tree or guest details.
4. **Cloud Run mitigation:** increase memory, reduce `containerConcurrency`, and raise `maxScale` if appropriate. Treat this as mitigation, not root-cause repair, when the frontend/backend shape still fans out full rebuilds.

## Reporting notes

Report the narrative with quantified evidence:

- 503 count in the window
- memory limit and observed overage
- exact endpoints involved
- Cloud Run config: memory, cpu, concurrency, maxScale
- frontend file/function causing fan-out
- backend file/function causing full-tree rebuild
- BigQuery snapshot count and JSON size summary

Avoid saying “Cloud Run is down” when the service is Ready and only specific request bursts OOM the instance.