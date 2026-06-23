# Cloud Run Job API route and VPC egress debugging

Use this when a Cloud Run Job calls an HTTP API and fails, but the same URL works from a laptop or local shell.

## Pattern observed

A `passenger-survey-dashboard-jobs` Cloud Run Job in `dev-cola-rd` failed at Stage 1 with repeated HTTP 404 from:

- `/report/customer-feedback/ai-label`

Important twist: after changing the hard-coded production host to the canonical Cloud Run service URL discovered from `gcloud run services list`, local requests to the same full URL and query parameters returned HTTP 200 with valid JSON, but the Cloud Run Job still returned HTTP 404.

The Job had:

- Direct VPC configured
- `vpc-egress: all-traffic`
- network tags including the environment label
- Cloud Build post-deploy execution enabled

This means the failure was no longer simply a stale URL. It became an environment-specific route/egress problem: the Job’s runtime network path did not behave like the local/public Internet path.

## Triage checklist

1. Confirm exactly what the Job is running.
   - `gcloud run jobs describe JOB --project=PROJECT --region=REGION`
   - Check image tag, `taskCount`, `parallelism`, env vars, service account, VPC, `vpc-egress`, latest execution.

2. Check latest executions.
   - `gcloud run jobs executions list --job=JOB --project=PROJECT --region=REGION --limit=5`
   - Describe the latest execution and read task failures.

3. Read execution logs, not just Cloud Build status.
   - Filter by `resource.type="cloud_run_job"`, `job_name`, `location`, and execution name.
   - Verify the full request URL in application logs.
   - Count retry attempts and task indexes.

4. Compare local/public URL behavior.
   - Call the exact full URL and date parameters from a non-Cloud-Run environment.
   - Record status, content-type, top-level JSON keys, data count, and a few schema keys.
   - Treat local HTTP 200 as evidence that the endpoint exists, not proof that the Job can reach the same backend.

5. Check whether the upstream service logs saw the request.
   - Query the upstream service’s project logs during the Job failure window.
   - If the Job logs show 404 but upstream service logs show no matching request, suspect VPC egress/DNS/routing/load-balancer/Host-path differences rather than application route absence.

6. Verify Cloud Build side effects.
   - Some `cloudbuild.yaml` files deploy the Job and then immediately execute it.
   - After every deploy, inspect the automatically-created execution and its logs.

7. Avoid duplicate ETL tasks.
   - If the code does not read Cloud Run task index and shard work, set `taskCount=1` and `parallelism=1`.
   - Otherwise multiple tasks may call the same API range and write the same sink.

## Durable fix recommendations

- Prefer runtime env var overrides for upstream URLs over hard-coded constants:
  - `AI_LABEL_URL`
  - `LABEL_ANALYZE_URL`
  - or `FEEDBACK_SURVEY_BASE_URL`
- Log the resolved URL before calling it.
- Add a small `--stage stage1 --dry-run` or `--api-smoke-test` path if supported, so deployment can verify route reachability before BigQuery writes.
- When local 200 / Cloud Run 404 diverge, run a minimal debug Cloud Run Job using the same service account, VPC, subnet, egress mode, and network tags to `curl -i` the endpoint and print response headers/body preview.
- Consider testing `vpc-egress=private-ranges-only` or removing all-traffic egress if the target is a public Cloud Run URL and the VPC path is suspected.

## Reporting shape

Report separately:

- Build status and commit/image tag.
- Job config: task count, parallelism, VPC egress, service account, env vars relevant to routing.
- Execution status: execution id, task count, succeeded/failed/retried counts.
- Route evidence: exact URL in logs, local status, Job status, upstream service log presence/absence.
- Remaining uncertainty: whether the canonical host is the business-approved endpoint vs merely a technically live endpoint.