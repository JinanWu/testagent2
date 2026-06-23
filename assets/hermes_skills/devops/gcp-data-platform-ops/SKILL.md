---
name: gcp-data-platform-ops
description: Map, troubleshoot, and repair GCP-backed data platforms spanning BigQuery, Cloud Run, scheduled jobs, and multi-repo system intake.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [GCP, BigQuery, Cloud Run, data-platform, architecture, troubleshooting]
---

# GCP Data Platform Ops

Use this skill when the task is to understand, troubleshoot, or repair a GCP-backed data platform or service stack.

## What this umbrella covers

- **System / architecture intake**: build a living map of repos, environments, tables, jobs, and dependencies.
- **BigQuery repair / backfill**: inspect schemas, back up tables, reconcile source vs target, and verify repairs safely.
- Cloud Run + GenAI troubleshooting: triage latency, timeout, deployment, quota, and upstream model-call issues.
- Vertex AI Gemini tuned model operations: discover fine-tuned Gemini models across environments, identify deployed endpoints/checkpoints, and run minimal endpoint smoke tests before broader validation.

## Shared approach

1. Identify the exact resource names first.
   - project, dataset, table
   - service, revision, region
   - job, schedule, delivery target
2. Verify live evidence before assuming causality.
   - CLI output, logs, schema, or request traces
3. Keep repairs and explanations bounded to the relevant window or service.
4. Prefer compact summaries and sample rows/log lines over huge dumps.

## System / architecture intake subsection

- Start by mapping stable nouns: repos, entrypoints, environments, schedulers, tables, consumers, and external dependencies.
- Distinguish prod from dev and record drift explicitly.
- Build the map in layers: product goal, repo roles, runtime flow, environments, data stores, triggers, risks.
- Verify with repo docs and live CLI output rather than guesses.

## BigQuery repair / backfill subsection

- Confirm project, dataset, table, and schema before touching data.
- Measure the target window and key coverage before any write.
- Create a backup before repair operations.
- Validate the repair source and join keys; do not guess the business key.
- For expensive derived-field repairs such as VECTOR_SEARCH consensus or model-inference backfills, use deterministic sharding, stage tables, and a single final MERGE; see `references/bigquery-async-backfill-patterns.md`.
- Before launching model-inference backfills, verify the feature vector dimension and run a 1-2 row local smoke test through the exact model. Do not assume a dashboard `embedding_vector` column matches the classifier input; if dimensions differ, regenerate the model-specific feature representation without rewriting the dashboard embedding unless explicitly requested. See `references/bigquery-ml-backfill-field-dimension.md` and `references/passenger-survey-rebuild-backfill-checklist.md`.
- When parallelizing BigQuery/model backfills, prefer one stage table per worker and UNION ALL later rather than concurrent writes/MERGEs to the same target.
- When a derived field depends on another derived field (for example consensus depends on labels), do not declare the rebuild complete until the downstream derived pass is rerun and verified against the newly-filled rows.
- For read-only BigQuery verification under restricted permissions, prefer `bq query` aggregates and schema reads over the Python BigQuery client if the client cannot create jobs or lacks project-use permission. Keep the verification path strictly SELECT-only when the user forbids writes; see `references/passenger-survey-stage2-no-write-verification.md`.
- When sampling BigQuery `embedding_vector` values for local sklearn / numpy checks, normalize elements to numeric floats before model input. A BigQuery `REPEATED FLOAT` column can still arrive in client code as strings, so assert and convert the element type before KNN/consensus work.
- For long-running local backfills on a user's MacBook, explicitly prevent idle sleep for the expected duration plus buffer (for example `caffeinate -dimsu -t <seconds>`) and verify `pmset -g assertions`; report the assertion/process ID and expiration time.
- For long-running backfill progress reports, include process/job IDs, exact processed counts vs totals, stage-table counts, whether data has been MERGEd back to the main table yet, ETA, and whether logs/jobs show errors or stuck signals. If a superseded process was intentionally killed and relaunched, distinguish the old `-15`/SIGTERM from the active replacement process.
- Re-run counts and spot-check repaired rows after the write. Also review quality/status flags after successful backfills; stale `needs_*` flags can make a clean table look unprocessed to downstream dashboards.
- Before replacing a production BigQuery table from a rebuild, create a clean final table that drops temporary rebuild/audit columns, keeps only approved business additions, verifies original-key coverage and schema deltas, and backs up the original table before swapping. For passenger-survey-style details see `references/passenger-survey-rebuild-backfill-checklist.md`.
- Treat helper tables as helpers, not proof that they match the target window.
- For staged rebuilds, reconstruct each stage from SQL/job text, then report purpose, inputs, outputs, verification result, and remaining gaps stage-by-stage.
- For dashboard trees/hierarchies built from BigQuery rows, verify both structural readiness (grouping/display fields and taxonomy mapping coverage) and scoring readiness (metric fields populated); report mapped vs unmapped prefixes before saying the tree can fully build. See `references/bigquery-dashboard-tree-readiness.md`.
- When a dashboard drilldown stops even though source rows exist, check for a variable-depth tree vs fixed-depth front-end contract mismatch: inspect source counts, Stage3 leaf depth, adapter node keys, and front-end child-property assumptions before calling it a data gap. See `references/bigquery-dashboard-variable-depth-tree-debugging.md`.
- When the product/UI entrypoint taxonomy intentionally differs from the stored BigQuery root taxonomy, use a narrow adapter/frontend alias instead of a broad variable-depth rewrite: output `(display_name, node, source_path)`, keep summary/trend on the source path, hide only the redundant root entry, and make frontend marker ids match the API node id rather than the display label. See `references/bigquery-dashboard-virtual-root-entrypoint.md`.
- When the user wants the production tree fixed and recomputation is cheap, prefer backing up the formal BigQuery snapshot table, rebuilding the correct fixed-depth tree, and replacing the snapshot directly over temporary backend hacks that must later be reverted. See `references/passenger-survey-fixed-depth-tree-rebuild.md`.
- When the user wants to directly preview the front-end tree but says not to build summaries, run a metrics-only Stage3 snapshot against the formal dashboard source/snapshot tables, bypass only Gemini summary generation with an empty summary tree, then verify both BigQuery latest snapshot fields and the dashboard adapter's nested hierarchy/leaf paths. See `references/passenger-survey-stage3-metrics-only-snapshots.md`.
- Before replacing a dashboard snapshot table with `WRITE_TRUNCATE`, verify whether it also carries historical trend rows. Derive the rebuild windows from the formal source table's `MIN/MAX(tour_date)` instead of hardcoding the latest month(s), otherwise historical charts can disappear even though source data is intact. If a mistaken replacement already happened, back up that intermediate state before the corrective overwrite. See `references/passenger-survey-stage3-history-preserving-rebuild.md`.
- When a dashboard trend panel says history failed but KPI/hierarchy data loads, separate four layers before fixing: frontend fallback wording, lazy trend endpoint status, active backend process/worktree, and BigQuery snapshot/source availability. Compare the API `source.runId` against the latest `opinion_tree_metrics_summary_snapshot`, and inspect the recursive JSON shape (`level_weighted_mean` / `head_weighted_mean`) before assuming `$.metrics.*` fields exist. See `references/passenger-survey-dashboard-trend-api-debug.md`.
- When Cloud Run 503s cluster on dashboard trend/detail endpoints while the revision is Ready, check for cache-stampede OOM rather than assuming the BigQuery data is inherently too large: count 5xx by second/path, correlate memory-limit/SIGKILL platform logs, inspect frontend fanout, and measure single cold-build RSS. For sync FastAPI endpoints, guard expensive module-level hierarchy cache construction with `threading.RLock`/double-checked singleflight and put `force_reload` invalidation under the same lock. See `references/cloud-run-cache-stampede-oom.md`.
- When dashboard trend/detail endpoints return 503 while the Cloud Run revision is Ready, check for frontend fan-out plus backend full-tree rebuilds before blaming BigQuery availability: eager child trend prefetch can launch many `/trend` requests, and each cold-cache request may independently materialize full hierarchy/history until Cloud Run OOM-kills the instance. Correlate `httpRequest.status=503` with `Memory limit ... exceeded`, `signal 9`, endpoint bursts, Cloud Run `containerConcurrency`/`maxScale`, and code paths. See `references/cloud-run-dashboard-trend-oom-fanout.md`.
- If historical passenger-survey rows are missing `tour_date` and the user explicitly accepts approximate recovery, implement `DATE(create_time)` as a bounded fallback, drop rows missing both dates, and make the approximation visible in counts/reports. If those rows also lack `tour_code`/`tour_name`, route them to an explicit `未分類 / 無團號 / 依 create_time 估算` branch rather than silently dropping them or assigning fake product lineage. When the user asks to carry a one-time repair's logic into ETL, port only the durable transformation/read/export semantics and tests; do not port one-time backup/rebuild/replace/backfill operations. See `references/passenger-survey-stage3-onetime-date-fallback.md`.
- When loading rebuilt rows into BigQuery JSON columns via Python `load_table_from_json`, pass nested dict/list values for JSON fields rather than pre-serialized JSON strings; otherwise `JSON_VALUE(metrics_tree, '$.field')` can return null because the JSON column contains a JSON string.
- When a dashboard snapshot has a correct `metrics_tree` but empty/slow/misaligned `summary_tree`, separate structural repair from semantic summary quality: build a deterministic extractive summary tree that exactly mirrors `metrics_tree.children`, validate node counts and known product/tour paths, write it with an explicit non-Gemini `summary_model`, and offer a later Gemini quality pass only if needed. See `references/passenger-survey-stage3-summary-tree-validation.md`.
- When a dashboard summary appears truncated or stops mid-sentence, verify frontend hard string/CSS truncation, backend path mapping, and the exact stored BigQuery `summary_tree` node before attributing blame. If BigQuery already ends mid-sentence and frontend renders raw `ctx.summary`, report that the UI is consistent with storage and the issue is upstream summary generation/persistence. See `references/bigquery-dashboard-summary-truncation-check.md` for the recipe and regression-test shape.
- When a passenger-survey dashboard trend chart shows `歷史趨勢暫時載入失敗`, first separate BigQuery data presence from live backend routing: query `opinion_tree_metrics_summary_snapshot` for monthly rows, test `/dashboard/api/v1/satisfaction/trend?node_type=root&node_id=root`, inspect `/openapi.json`, and verify the listening uvicorn PID/cwd. A stale backend from another worktree on port 8000 can 404 the trend endpoint while the current repo and BigQuery are healthy. See `references/passenger-survey-dashboard-trend-api-debugging.md`.
- When the user does want the full Gemini semantic quality pass for a passenger-survey Stage3 `summary_tree`, build summaries bottom-up from child summaries, use a per-node checkpoint cache, prefer request timeouts/REST over unbounded SDK calls if needed, protect local MacBook runs with `caffeinate`, and validate recursive child-key parity with `metrics_tree` before writing BigQuery. Report process IDs, cache progress, source/target tables, date range, model, row counts, pending validation gates, and — for long runs — explain why it is slow in terms of cache hits vs real Gemini calls, child-before-parent dependencies, current tree path, and remaining nodes. See `references/passenger-survey-stage3-gemini-hierarchical-summary-pass.md`.
- When a generated `summary_tree` node ends mid-sentence, compare frontend/API/BigQuery/cache layers first. If the same partial text is already in the Gemini checkpoint cache, inspect the model-call wrapper for missing `candidate.finishReason` validation and insufficient output budget; Gemini 2.5 thinking tokens can consume much of `maxOutputTokens`. Require `finishReason == STOP`, log token usage, reject suspicious sentence endings, and invalidate bad cache entries before rewriting snapshots. See `references/passenger-survey-gemini-summary-truncation.md`.
- For large BigQuery tables, prefer aggregate verification (`COUNTIF`, grouped counts, distinct-key counts) and only 2-3 sample rows; do not download or dump large tables.
- For dashboard opinion search that combines keyword matching with existing BigQuery embeddings, first confirm embedding completeness/dimension/model lineage, reuse the ETL model settings for query embeddings, preserve existing filters, expose score fields for rollout inspection, and keep a LIKE fallback. See `references/bigquery-dashboard-hybrid-opinion-search.md`.
- When cleaning a BigQuery dataset by deleting backup/stage/temp tables, build an exact protected keep-list first, guard against deleting formal tables and `_SEARCH_INDEX_*` objects, delete one table at a time, then verify with `bq ls` plus formal row counts/date ranges. If fresh backups are needed after cleanup, use timestamped `bq cp` and verify row count + schema column count. See `references/bigquery-dataset-cleanup-and-rebackup.md`.
- When validating whether a Stage1 ETL is production-ready, separate data-shape readiness from local runtime readiness: verify source API rows, schema/serialization, MERGE safety, and BigQuery aggregate quality independently from ADC/quota-project/Vertex permission blockers.
- When validating whether a Stage1 ETL is production-ready, separate data-shape readiness from local runtime readiness: verify source API rows, schema/serialization, MERGE safety, and BigQuery aggregate quality independently from ADC/quota-project/Vertex permission blockers. If embedding is blocked locally, a deterministic fake-embedding `dry_run=True` probe can validate transformation shape, but must not be reported as a successful full run. See `references/passenger-survey-stage1-etl-readiness.md`.
- For passenger-survey / mood-index ETL validation that reads prod APIs but writes dev BigQuery, first verify the environment split explicitly: prod API flag, dev BigQuery target, and Vertex AI project/quota project may be three different projects. Back up dev target/report tables before writing, validate Stage1 and Stage2 counts separately, and stop before Stage3 snapshot append if sentiment scoring fields are blank or `scored_count=0`. See `references/passenger-survey-prod-api-dev-bq-etl-validation.md`.
- When dev Stage3 metrics are blocked only because `ai_sentiment_label` / `ai_sentiment_score` are blank, use a dev-only sentiment backfill that updates only those two fields after a small dry-run, then verify label distribution and Stage3 `scored_count`/weighted means. If Gemini summary generation hangs after metrics are computed, separate the layers: write an explicitly labeled metrics-only validation snapshot/stub and recommend timeout/progress/fail-open summary controls before claiming full Stage3 pass. See `references/passenger-survey-dev-sentiment-backfill-stage3-validation.md`.
- For BigQuery jobs, `DONE` is only terminal state: always check `status.errorResult`/`status.errors` before calling a job successful.
- Related session-specific notes for no-write Stage2 verification live in `references/passenger-survey-stage2-no-write-verification.md`.

## Cloud Run + GenAI subsection

- First determine whether the service is actually ready and serving.
- When a Cloud Run service deployed from one repo must call different Vertex/Gemini tuned endpoints in dev and prod, verify whether the user wants endpoint selection controlled by Cloud Build trigger substitutions or by existing project variables inside `cloudbuild.yaml`. If they ask for the deployment file to use project variables, implement a bash deploy step that branches on `${PROJECT_ID}`, sets `GEMINI_MODEL_PROJECT_ID` / `GEMINI_MODEL_LOCATION` / `GEMINI_MODEL_ENDPOINT`, uses `$${VAR}` for shell-time expansion, and fails closed for unknown projects. Do not update triggers or live Cloud Run when the requested scope is only `cloudbuild.yaml`; see `references/cloudbuild-env-specific-vertex-endpoints.md`.
- For Vertex AI Gemini tuned-model migrations, first list and describe candidate models/endpoints in the target project/environment, ask the user to select the test target if multiple candidates exist, then run a minimal callability smoke test before broader functional tests. See `references/vertex-gemini-tuned-model-smoke-test.md`.
- Separate rollout state, serving limits, and upstream model/API behavior.
- When deploying Cloud Run Jobs from Cloud Build triggers, verify the live trigger substitutions and final Job config rather than trusting repo defaults alone; trigger substitutions can preserve stale `_BUILD_ARG_API_ENV`, task counts, or env vars. For unsharded ETL jobs, keep `taskCount=1` unless code reads Cloud Run task index and partitions work.
- When migrating a prediction repo to a Vertex AI Gemini tuned model endpoint, first list/describe tuned models and endpoints in the dev project, then make a minimal direct endpoint call before editing app logic. Use environment variables for endpoint settings, parse JSON/fenced-JSON output by label name, and smoke-test Flask Pub/Sub payloads with `SKIP_PUBSUB_PUBLISH=1`; see `references/vertex-gemini-tuned-model-migration.md`.
- When replacing an in-repo classifier with a Vertex AI Gemini tuned endpoint, first list/describe tuned models and endpoints, then run a minimal `google.genai.Client(...).models.generate_content()` endpoint probe before changing service code. Preserve schema compatibility, parse JSON robustly, and avoid loading unused old local models on startup; see `references/vertex-gemini-tuned-endpoint-migration.md`.
- When a production Cloud Run prediction service logs Vertex AI/Gemini `403 PERMISSION_DENIED` with `VPC_SERVICE_CONTROLS`, compare the service runtime project/service account with the configured `GEMINI_MODEL_PROJECT_ID`, `GEMINI_MODEL_LOCATION`, and full endpoint resource. A prod service may be silently using a dev tuned-model endpoint from code defaults if Cloud Run env vars are missing; HTTP 200 can still hide all-false label fallback. See `references/passenger-survey-pred-gemini-endpoint-env.md`.
- When a dev Cloud Run Job fails immediately after deploy while calling an upstream API, verify build/runtime environment selection before assuming the dev upstream is broken: `cloudbuild.yaml` may bake `_BUILD_ARG_API_ENV=production`, Dockerfile/dotenv selection may copy `.env.production`, and the dev job may call a prod `*.run.app` URL. Query execution-scoped job logs, report task/retry counts, and treat a later successful curl as only current-state evidence, not as disproving the execution-time 404. See `references/cloud-run-job-post-deploy-api-env-404.md`.
- When the exact API URL returns 200 locally but a Cloud Run Job gets 404, do not stop at changing the hard-coded host. Compare the Job's VPC/subnet/`vpc-egress`/network tags and check whether the upstream service logs saw the request; `all-traffic` VPC egress can create a different route than local public Internet. Also verify Cloud Build post-deploy executions and reduce `taskCount` to 1 unless the code shards by task index. See `references/cloud-run-job-api-route-and-vpc-egress-debug.md`.
- For dashboard ETL jobs that call `call_ai_label_api()`, a 404 from `/report/customer-feedback/ai-label` means the upstream route or environment mapping is wrong until proven otherwise. Verify the resolved URL, the job’s `api_env`, and whether the upstream service exposes `ai-label` versus a different sibling endpoint before chasing ETL/data bugs. See `references/passenger-survey-dashboard-job-ai-label-404.md`.
- When local calls to a feedback-survey API return 200 but the Cloud Run Job returns 404, do not keep changing hosts blindly: compare local URL resolution, deployed-image logs, upstream service logs, Direct VPC/`vpc-egress=all-traffic`, and cloud-team dev→prod policy. Prefer runtime URL overrides (`AI_LABEL_URL`, `LABEL_ANALYZE_URL`, production/non-production variants) and explicit `API_ENV` over hard-coded hosts. See `references/passenger-survey-cloud-run-job-api-routing.md`.
- For passenger-survey Cloud Run Jobs that read prod survey APIs into dev BigQuery, verify the current upstream Cloud Run service URL via `gcloud run services list/describe`, smoke-test `/ai-label` and `/label-analyze`, and reduce non-sharded jobs to `taskCount=1`; `taskCount>1` is duplicate work unless the code explicitly shards by task index. See `references/passenger-survey-cloud-run-job-api-target.md`.
- Inspect logs around the slow request window, including stderr and request logs.
- Long latency without quota errors often points to fan-out, retries, or upstream slowness.
- Confirm whether observed 429 / RESOURCE_EXHAUSTED errors are hard quota pressure or just one part of a broader failure pattern.
- For batch inference services, always compare request latency with application-level batch start/completion logs; HTTP 200 does not imply healthy throughput.
- When reproducing operational load, model user traffic as call rate (for example N API calls/minute with small random batches) unless the user explicitly asks for a single large batch. Do not conflate "50/min" with one request containing 50 images.
- When clients time out, verify whether the server continues to process queued work and emits `開始批次辨識` / timeout / completion logs later; phrase causality as backend queueing causing the client to give up first, not client timeout causing backend timeout.
- When Hermes background-process watch notifications show app logs, first compare the process command/path/bind address before attributing them to Cloud Run. A command like `/Users/... && hypercorn ... --bind 127.0.0.1:8080` is local Mac execution even if it uses a GCP `PROJECT_ID`; only Cloud Run request/app logs prove Cloud Run activity.
- When a user asks for call counts in an exact local-time incident window, convert the window to UTC explicitly, query Cloud Run request logs for the target endpoint, and report request count separately from downstream Gemini/image-call count.
- When the user expects progress visibility, verify whether the code emits per-item in-progress markers or only batch-level start/finish logs; if missing, note that as an observability gap rather than a data-plane failure.
- Use trace-level drill-down for the slowest requests when request logs show repeated ~120s plateaus with no HTTP errors.
- Session-specific Cloud Run log triage notes and exact queries live in `references/cloud-run-passport-recog-triage.md`.
- Passport-recog rate-call reproduction steps, local Hypercorn setup, observed 10/20/30 calls/min results, and mitigation hypotheses live in `references/passport-recog-rate-call-reproduction.md`.
- When Cloud Run request bytes/latency exist but app batch-start logs are missing, inspect server/adapter queueing and bounded executor starvation; Flask wrapped by `WsgiToAsgi` may serialize WSGI route execution, and `asyncio.wait_for()` does not kill blocking `run_in_executor` threads. See `references/cloud-run-wsgi-threadpool-starvation.md`.
- For passport-recog-data specifically, compare `ASGI_ACCEPT` to Flask `REQ_START`/`BATCH_HANDLER_START` trace IDs. If many requests have `ASGI_ACCEPT` only, or `ASGI_ACCEPT -> REQ_START` grows to minutes while `REQ_START` shows `ThreadPoolExecutor-0_0` and `inflight=1`, the root cause is WsgiToAsgi/WSGI dispatch serialization rather than Gemini queue saturation. See `references/passport-recog-wsgitoasgi-serialization-root-cause.md`.
- Compare failure shape, not only severity: prod incidents may be “HTTP 200 but 90–125s latency plateau,” while reproductions may produce HTTP 504 or client read timeouts; report this as partial reproduction unless the HTTP/status behavior matches.
- `references/cloud-run-passport-recog-triage.md`: concrete Cloud Run log queries and observations for passport-recog-data latency / partial-success incidents.
- `references/passport-recog-rate-call-reproduction.md`: rate-call load-test pattern for passport-recog-data, local Hypercorn reproduction setup, 10/20/30 calls/min results, and client-timeout-vs-server-continuation interpretation.

## Pitfalls

- Do not assume a successful 200 means the service is healthy if latency is extreme.
- Do not mutate BigQuery data before understanding the join coverage and backup path.
- Do not run large VECTOR_SEARCH backfills as one monolithic query when a deterministic shard/stage/MERGE pattern would reduce memory pressure and create retry boundaries.
- Do not parallelize local/ML backfills by having workers MERGE into the main table directly; write per-worker stage tables and do one final MERGE.
- Do not call a production Pub/Sub/Cloud Run prediction service for a one-field historical backfill if the service also performs unnecessary side effects such as embedding calls or answer-topic publishing; load the needed model directly in a batch worker when possible.
- Do not collapse dev and prod into one generic environment.
- Do not widen searches blindly when a narrow window and schema check would answer the question.
- Do not describe a background/local process as fully durable asynchronous work without noting whether the driver depends on the user's machine staying awake; for MacBook users, consider Cloud Run Jobs for long backfills.
- Do not pre-serialize BigQuery JSON-column payloads when using `load_table_from_json`; pass nested dict/list objects so `JSON_VALUE` sees a JSON object rather than a JSON string.
- Do not use a full per-node Gemini post-order summary pass as the first fix for a large dashboard tree when the user needs structural correctness now; validate shape with a deterministic extractive summary tree first, then optionally run a separate semantic-quality pass.
- Do not treat a full Gemini summary pass as safe just because JSON parsing succeeds; require recursive `metrics_tree`/`summary_tree` child-key parity, known-path checks, and a non-empty root summary before appending or replacing the BigQuery snapshot.
- Do not accept Gemini summary text solely because `content.parts[].text` is non-empty. Check `finishReason`, token usage, and sentence completeness; otherwise `MAX_TOKENS` or other non-STOP candidates can persist mid-sentence summaries into cache and BigQuery.

## Verification habits

- Architecture intake: confirm the map against repo/docs/CLI evidence.
- BigQuery repair: validate backup, coverage, and post-write row counts.
- Cloud Run: validate readiness, request timing, and upstream call patterns.

## Related support files

This umbrella intentionally keeps the class-level guidance here; session-specific repair recipes, log triage examples, and system maps belong in support files under the umbrella.

- `references/bigquery-vector-and-ml-backfill.md`: patterns for chunked BigQuery VECTOR_SEARCH repairs, asynchronous ML sentiment backfills, stage-table MERGE workflows, and durability trade-offs for local vs Cloud Run Jobs.

- `references/bigquery-dashboard-hybrid-vector-search.md`: pattern for adding keyword + embedding hybrid search to BigQuery-backed dashboards, including embedding model discovery, bounded candidate SQL, score fields, and focused verification.

- `references/passenger-survey-rebuild-backfill-checklist.md`: concrete aggregate-verification checklist for dashboard rebuilds with model inference, embedding lineage mismatches, dependent consensus passes, stale quality flags, and macOS `caffeinate` protection for local drivers.

- `references/bigquery-ml-backfill-field-dimension.md`: model-inference backfill checks for feature-vector dimension mismatches, local smoke tests, per-worker stage verification, and reporting intentionally killed superseded processes separately from active replacements.

- `references/bigquery-model-inference-embedding-lineage.md`: checks and fallback patterns for model-inference backfills when stored `embedding_vector` dimension/model lineage may not match the classifier’s expected input.

- `references/bigquery-staged-rebuild-and-verification.md`: aggregate-only verification pattern for staged BigQuery rebuilds, multi-statement job timeout checks, and stage-by-stage reporting.

- `references/bigquery-dashboard-tree-readiness.md`: checks for BigQuery-backed dashboard tree/hierarchy readiness, including structural fields, scoring fields, taxonomy/prefix mapping coverage, and passenger-survey stage3 artifact pitfalls.

- `references/bigquery-dashboard-variable-depth-tree-debugging.md`: debugging pattern for UI drilldowns that stop because Stage3/adapter emits variable-depth leaves while the front-end expects fixed region/line/group/product/tour levels.

- `references/bigquery-dashboard-virtual-root-entrypoint.md`: precise adapter/frontend alias pattern for dashboards whose UI entrypoints intentionally differ from stored BigQuery root taxonomy, including display-name vs source-path ids and hiding redundant roots.

- `references/passenger-survey-stage3-onetime-date-fallback.md`: one-time passenger-survey Stage3 repair pattern for missing `tour_date` rows using `DATE(create_time)`, discarding rows missing both dates, and preserving no-code fallback rows under an explicit unclassified branch.

- `references/passenger-survey-stage3-summary-tree-validation.md`: repair/verification pattern for passenger-survey Stage3 snapshots whose `metrics_tree` is correct but `summary_tree` is empty, slow to generate, or structurally misaligned; includes deterministic extractive fallback and recursive shape checks.

- `references/passenger-survey-stage3-gemini-hierarchical-summary-pass.md`: full semantic-quality pass for passenger-survey Stage3 `summary_tree`; covers bottom-up child-summary rollups, checkpoint cache/resume, Vertex Gemini REST timeout pattern, Mac `caffeinate` protection, and pre-write validation gates.

- `references/passenger-survey-stage1-etl-readiness.md`: Stage1 readiness audit pattern for passenger-survey ETL, including API probing, ADC/quota-project checks, dry-run/fake-embedding shape validation, BigQuery aggregate checks, and reporting gates.

- `references/passenger-survey-prod-api-dev-bq-etl-validation.md`: prod survey API to dev BigQuery validation recipe, including lab Vertex embedding project split, pre-write backups, Stage1/Stage2 aggregate verification, and Stage3 scoring-readiness stop conditions.
