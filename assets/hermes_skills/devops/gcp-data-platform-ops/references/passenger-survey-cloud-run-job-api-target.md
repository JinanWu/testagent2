# Passenger-survey Cloud Run Job API target and task-count triage

Use this when a `passenger-survey-dashboard-jobs` Cloud Run Job fails near Stage 1 with repeated `404 Client Error` from `/report/customer-feedback/ai-label`, or when dev should read prod survey APIs but write dev BigQuery.

## Durable pattern

1. Verify the exact Cloud Run Job and latest execution first:
   - `gcloud run jobs describe passenger-survey-dashboard-jobs --project=dev-cola-rd --region=asia-east1 --format=yaml`
   - `gcloud run jobs executions describe <execution> --project=dev-cola-rd --region=asia-east1 --format=yaml`
   - Query execution logs filtered by `labels."run.googleapis.com/execution_name"`.

2. Do not stop at the URL printed in the failing app log. Resolve the current upstream service URL from Cloud Run:
   - `gcloud run services list --project=prod-cola-rd --region=asia-east1 --format='table(metadata.name,status.url)'`
   - For this pipeline, the intended prod upstream observed during the session was `feedback-survey-service-bv7ztwxyfa-de.a.run.app`, while the code/job image still called `feedback-survey-service-586561863834.asia-east1.run.app`.

3. Smoke-test the exact endpoint and date window before redeploying:
   - `GET /report/customer-feedback/ai-label?startDateTime=...&endDateTime=...`
   - Also test `/report/customer-feedback/label-analyze` if Stage 2 will run.
   - A 200 with a small preview is enough; do not dump full payloads.

4. Check whether the job is accidentally doing duplicate work:
   - `taskCount: 4` with no task-index sharding means four tasks run the same ETL, calling the same API/date window and risking duplicate BigQuery/API load.
   - Prefer `taskCount: 1`, `parallelism: 1` unless the code explicitly reads Cloud Run task index and shards work deterministically.

5. Update repo/deploy config, not just one manual execution, when the fix is durable:
   - `embedding_pipeline/constants.py`: `PRODUCTION_AI_LABEL_URL` and `PRODUCTION_API_URL` should point to the verified prod service URL.
   - `cloudbuild.yaml`: `_JOB_TASKS: "1"` for the non-sharded job.
   - Include runtime env vars needed by the job (`API_ENV=production`, dev BigQuery project/dataset/table, Vertex location/model, stage3 report table, quota project) in deployment config where appropriate.

6. Watch for deployment side effects and permissions:
   - This repo's `cloudbuild.yaml` includes a post-deploy `gcloud run jobs execute` step; deploying can immediately run the job.
   - Direct `gcloud run jobs update` needs `run.jobs.update`.
   - `gcloud run jobs execute --tasks=1 --update-env-vars=...` needs `run.jobs.runWithOverrides`.
   - `gcloud builds submit` may fail if the user lacks access to the Cloud Build source bucket / `serviceusage.services.use`.

## Reporting shape

Report:
- target project/region/job/execution
- task count / parallelism before and after
- exact API URL class before and after
- smoke-test HTTP status for the new endpoint
- whether the online job was actually updated or only the repo was patched
- any IAM blocker separately from the technical fix

## Pitfalls

- A local curl returning 200 does not prove the deployed image is using that URL; inspect logs from the new execution after deployment.
- Do not call the issue fixed if the Cloud Run Job still references an old image/tag or old constants.
- Do not treat `taskCount > 1` as parallelization unless there is explicit task-index sharding in the code.
