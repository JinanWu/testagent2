# Cloud Run Job post-deploy execution with API_ENV drift and upstream 404

Use this reference when a Cloud Run Job fails immediately after deploy while calling an upstream API, especially when the job lives in a dev project but the failing URL points at production.

## Failure shape observed

- Resource class: Cloud Run Jobs (`resource.type="cloud_run_job"`).
- Example job: `passenger-survey-dashboard-jobs` in `dev-cola-rd`, region `asia-east1`.
- Latest execution failed with all tasks failing and `NonZeroExitCode`.
- Logs showed Stage 1 starting, then the first upstream API call failed:
  - `AI Label API 呼叫失敗，已達最大重試次數`
  - `requests.exceptions.HTTPError: 404 Client Error: Not Found`
  - traceback path: `cli.py -> run_pipeline -> run_stage1 -> call_ai_label_api -> response.raise_for_status()`
- The failing URL was a production Cloud Run service URL even though the job was in the dev project.

## Triage sequence

1. Identify latest execution and job config:

```bash
gcloud run jobs describe JOB --region=REGION --project=PROJECT --format=json

gcloud run jobs executions describe EXECUTION --region=REGION --project=PROJECT --format=json
```

Check:
- `latestCreatedExecution.completionStatus`
- `taskCount`, `failedCount`, `retriedCount`
- container image/tag/release
- runtime env vars such as `API_ENV`, `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`
- `maxRetries` and `parallelism`

2. Query execution-scoped logs, not just generic service logs:

```bash
gcloud logging read '
resource.type="cloud_run_job"
AND resource.labels.job_name="JOB"
AND resource.labels.location="REGION"
AND labels."run.googleapis.com/execution_name"="EXECUTION"
AND (severity>=ERROR OR textPayload:"已達最大重試次數" OR textPayload:"Client Error")
' --project=PROJECT --format='table(timestamp,labels."run.googleapis.com/task_index",labels."run.googleapis.com/task_attempt",severity,textPayload)' --limit=100 --order=asc
```

3. Separate the first failing step from downstream stages.

If logs show only `Stage 1` start and `步驟 1: 呼叫 AI Label API...`, do not investigate BigQuery, embedding, or later stages first; the pipeline has not reached them.

4. Inspect deploy config for hidden side effects and environment drift.

Look for:
- `cloudbuild.yaml` deploy step followed by `gcloud run jobs execute` (`post-deploy-execute`).
- build substitutions such as `_BUILD_ARG_API_ENV: production` in a dev project.
- Dockerfile `ARG API_ENV=production` and dotenv selection scripts that bake `.env.production` into the image.
- Runtime env only setting Sentry environment/release but not overriding application `API_ENV`.

5. Verify the upstream failure window if needed.

Query the upstream service logs in the project that owns the URL. A `*.run.app` URL containing the project number can reveal whether it is prod or dev. Example: `586561863834` mapped to `prod-cola-rd`, while `706707303745` mapped to `dev-cola-rd`.

If a direct curl later returns 200, report that the execution-time logs still prove the job failed on 404 at that time; phrase it as a transient upstream routing/deploy/window issue or an environment-selection issue, not as proof the endpoint is permanently broken.

## Reporting pattern

Include:
- project, region, job, execution name
- start/completion time in UTC and local timezone if relevant
- task count, failed count, retry count, max retries
- exact first failing URL and status
- first failing code path / traceback frame
- whether all tasks failed with the same pattern
- whether deploy config auto-ran the job after deployment
- whether dev job actually called prod URL due to build/runtime `API_ENV`

## Pitfalls

- Do not call a dev Cloud Run Job failure a dev upstream API failure until you verify the URL and project number. The job can be in dev while the baked config calls prod.
- Do not stop at `execution failed`; extract task-level logs and the first application error.
- Do not blame downstream ETL/BigQuery stages if logs show the first API call failed before any downstream work.
- Do not treat a later successful curl as disproving the logged failure. Execution logs are the evidence for what happened during that run.
- Note Sentry warnings separately. `SENTRY_DSN is set, but sentry-sdk is not installed` affects observability, not the data-plane root cause.
