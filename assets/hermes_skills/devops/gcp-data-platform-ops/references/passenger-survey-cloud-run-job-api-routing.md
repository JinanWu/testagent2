# Passenger survey Cloud Run Job API routing triage

Use this when `passenger-survey-dashboard-jobs` or similar ETL Cloud Run Jobs fail at Stage 1/2 while calling feedback-survey APIs.

## Durable lessons

1. Separate three questions before changing code:
   - Which URL does the code resolve locally?
   - Which URL does the deployed image actually call in Cloud Run logs?
   - Does that request reach the expected upstream service logs?

2. Local HTTP 200 does not prove Cloud Run can call the same URL. Dev Cloud Run Jobs may run with Direct VPC and `vpc-egress=all-traffic`, network tags, firewall/NAT/DNS policy, or organization restrictions that block or reroute dev -> prod calls. A Cloud Run Job seeing 404 while local sees 200 can be a network/routing/policy difference, not a bad path.

3. Verify Cloud Build trigger substitutions, not only `cloudbuild.yaml`. Trigger-level substitutions can override repo defaults. Check the live build with:
   - `gcloud builds describe BUILD_ID --project=PROJECT --region=REGION --format='yaml(substitutions,status,steps.id,steps.status,logUrl)'`

4. Check Cloud Run Job config after deploy:
   - image tag / release
   - `taskCount` and `parallelism`
   - runtime env vars
   - latest execution name/status

5. For ETL jobs with no task-sharding logic, use `taskCount=1`. Multiple tasks may repeat the same source API calls and BigQuery writes rather than splitting work.

## Recommended repo pattern

Avoid hard-coding a single prod API host in ETL code. Implement URL resolution with runtime environment overrides:

- `LABEL_ANALYZE_URL` and `AI_LABEL_URL` override full URLs directly.
- `PRODUCTION_LABEL_ANALYZE_URL` / `PRODUCTION_AI_LABEL_URL` override only production resolution.
- `NON_PRODUCTION_LABEL_ANALYZE_URL` / `NON_PRODUCTION_AI_LABEL_URL` override non-production resolution.
- Fall back to code constants only when no env override is set.

Cloud Build for dev should make the API environment explicit, for example:

- `_BUILD_ARG_API_ENV=development`
- runtime env includes `API_ENV=${_BUILD_ARG_API_ENV}`

If dev is intentionally allowed to read prod, configure the approved prod-access endpoint as runtime env and verify with Cloud Run logs. If dev is not allowed to call prod, do not keep retrying prod URLs; switch to the permitted non-prod endpoint or ask the cloud team for an approved proxy/egress path.

## Verification log checks

For a latest execution:

```bash
gcloud run jobs describe passenger-survey-dashboard-jobs \
  --project=dev-cola-rd --region=asia-east1 \
  --format='yaml(spec.template.spec.taskCount,spec.template.spec.parallelism,spec.template.spec.template.spec.containers[0].image,spec.template.spec.template.spec.containers[0].env,status.latestCreatedExecution)'

gcloud logging read 'resource.type="cloud_run_job" resource.labels.job_name="passenger-survey-dashboard-jobs" labels."run.googleapis.com/execution_name"="EXECUTION"' \
  --project=dev-cola-rd --limit=200 \
  --format='value(timestamp,labels."run.googleapis.com/task_index",severity,textPayload)' --order=asc
```

Report whether the failure is:

- still using the old URL,
- using the new URL but receiving 404,
- DNS/connectivity timeout,
- auth/permission failure,
- or a later ETL/BigQuery/Vertex stage failure.
