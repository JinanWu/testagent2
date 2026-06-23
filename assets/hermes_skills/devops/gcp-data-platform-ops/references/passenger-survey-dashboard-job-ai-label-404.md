# passenger-survey-dashboard-jobs / ai-label 404 triage

## Incident snapshot
- Job: `passenger-survey-dashboard-jobs`
- Project: `dev-cola-rd`
- Region: `asia-east1`
- Observed failed execution: `passenger-survey-dashboard-jobs-n69z7`
- Result: `4/4` tasks failed, `NonZeroExitCode`, each task exited `1`
- Scheduler observed for this job: `passenger-survey-dashboard-scheduler`, `0 8 * * 1`, `Asia/Taipei` (weekly Monday 08:00, not daily)

## Grounded evidence
- The failing task raised:
  - `requests.exceptions.HTTPError: 404 Client Error: Not Found`
- The requested URL was:
  - `https://feedback-survey-service-586561863834.asia-east1.run.app/report/customer-feedback/ai-label?startDateTime=2026-06-08T00%3A00%3A00&endDateTime=2026-06-15T23%3A59%3A59`
- Traceback anchor points:
  - `embedding_pipeline/cli.py -> main()`
  - `embedding_pipeline/orchestrator.py -> run_pipeline() -> run_stage1()`
  - `embedding_pipeline/api.py -> call_ai_label_api()`
- The Cloud Run Job image/env evidence showed production routing:
  - network tags included `production`
  - `SENTRY_ENVIRONMENT=production`
  - Cloud Build default `_BUILD_ARG_API_ENV=production`
  - logs showed the production `feedback-survey-service-...run.app` endpoint

## Interpretation
- This is a hard upstream 404, not a transient timeout, quota symptom, BigQuery issue, embedding issue, or Stage 3 summary issue.
- When the job fails at `call_ai_label_api()`, check the resolved API URL and the target service’s deployed route before diagnosing ETL logic or data quality.
- Repeated failures across multiple executions suggest a stable config/route mismatch rather than a one-off upstream outage.
- If a dev Cloud Run Job is intentionally configured as `api_env=production` to read production data, the upstream production service must expose the exact `/report/customer-feedback/ai-label` route; otherwise Stage 1 will always fail before Stage 2/3.

## Triage checklist
1. Identify the exact job, region, latest execution, and scheduler:
   - `gcloud run jobs describe passenger-survey-dashboard-jobs --project=dev-cola-rd --region=asia-east1 --format=yaml`
   - `gcloud run jobs executions list --job=passenger-survey-dashboard-jobs --project=dev-cola-rd --region=asia-east1`
   - `gcloud scheduler jobs list --project=dev-cola-rd --location=asia-east1`
2. Read execution-scoped logs and find the first failing boundary:
   - filter on `resource.type="cloud_run_job"`, `resource.labels.job_name`, location, and `labels."run.googleapis.com/execution_name"`
   - report the first HTTP status/URL and traceback anchor, not just final `NonZeroExitCode`
3. Confirm the job’s `api_env` and how `resolve_ai_label_url()` maps it.
4. Verify the upstream service really exposes `/report/customer-feedback/ai-label` in the target environment.
5. Compare dev job settings vs production-like labels to ensure the job is not pointed at a mismatched deployment.
6. Check whether the upstream service was renamed, re-routed, or split into a different endpoint family (for example `label-analyze` vs `ai-label`).
7. If `taskCount > 1`, verify the code actually reads Cloud Run task index / task count and shards work. If not, treat multiple tasks as duplicate ETL/API traffic and recommend `taskCount=1` until sharding exists.
8. Inspect `cloudbuild.yaml` for post-deploy side effects. This repo’s Cloud Build included a `post-deploy-execute` step that immediately runs the Job after deploy; do not deploy casually if the user only asked to prepare a fix.

## Recommended fix order
1. Decide whether dev job should read production API or SIT/dev API.
   - If production data is required, fix/deploy the production `/ai-label` route or update the ETL to the correct production endpoint.
   - If production data is not required, configure the job to a non-production API env.
2. Reduce `taskCount` to `1` unless a verified sharding implementation exists.
3. Deploy only after checking whether Cloud Build will auto-execute the job.
4. After deployment, run a narrow execution or date-window smoke test and verify logs before restoring broad scheduling.
5. Only after Stage 1 succeeds, evaluate Stage 2/3 behavior; do not conflate the current 404 with later summary-generation issues.

## Notes
- In this repo, `call_ai_label_api()` uses a separate endpoint resolver from `call_label_analyze_api()`.
- A 404 at this boundary should be reported as an upstream contract/routing issue, not as a BigQuery or Cloud Run job runtime failure.
- Cloud Run Job status tables can be misleading if only `Ready=True` is inspected; use latest execution `conditions`, `failedCount`, `retriedCount`, and execution logs.
