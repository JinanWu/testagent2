# Passenger-survey prediction Cloud Run: Gemini endpoint environment mismatch

Use this when `passenger-survey-pred` is deployed on Cloud Run and logs show Vertex AI / Gemini tuned-model failures, especially `403 PERMISSION_DENIED` with `VPC_SERVICE_CONTROLS`.

## Failure shape observed

- Cloud Run service: `passenger-survey-pred`
- Runtime project/region: `prod-cola-rd` / `asia-east1`
- Cloud Run service account: `prod-passenger-survey-pred@prod-cola-rd.iam.gserviceaccount.com`
- Application logs per request:
  - `Gemini 微調模型預測過程中發生錯誤: 403 PERMISSION_DENIED`
  - `reason: SECURITY_POLICY_VIOLATED`
  - `violations.type: VPC_SERVICE_CONTROLS`
  - `service: aiplatform.googleapis.com`
- HTTP requests may still return `200` because `predict_label()` catches the Gemini error, returns `{}`, then the route falls back to all label booleans `False`. Treat this as data-quality failure, not service health.

## Root cause pattern

The app has fallback defaults for Gemini endpoint configuration. If Cloud Run does not set `GEMINI_MODEL_PROJECT_ID`, `GEMINI_MODEL_LOCATION`, and `GEMINI_MODEL_ENDPOINT`, production may silently call the development tuned-model endpoint.

Known mismatch from the session:

- Running Cloud Run project: `prod-cola-rd` / project number `586561863834`
- Dev default in code:
  - `GEMINI_MODEL_PROJECT_ID=dev-cola-rd`
  - `GEMINI_MODEL_LOCATION=us-central1`
  - `GEMINI_MODEL_ENDPOINT=projects/706707303745/locations/us-central1/endpoints/3740143283263766528`
- Correct deployed prod endpoint found:
  - `GEMINI_MODEL_PROJECT_ID=prod-cola-rd`
  - `GEMINI_MODEL_LOCATION=us-central1`
  - `GEMINI_MODEL_ENDPOINT=projects/586561863834/locations/us-central1/endpoints/901309813462401024`
  - deployed model at that endpoint: `projects/586561863834/locations/us-central1/models/4430592605041983488`

Note: prod also had another `passenger 2.0` model (`1831734145072496640`) that was not deployed on the passenger-survey endpoint. Prefer the deployed endpoint resource over guessing from model display names.

## Triage recipe

1. Describe Cloud Run service and revision env vars:
   - `gcloud run services describe passenger-survey-pred --region=asia-east1 --project=prod-cola-rd --format='yaml(spec.template.spec.containers[0].env,status.traffic,status.url)'`
   - `gcloud run revisions describe <revision> --region=asia-east1 --project=prod-cola-rd --format='yaml(spec.containers[0].env,spec.serviceAccountName,status.conditions)'`
2. Query recent logs for Gemini errors and request counts. Count POST request logs separately from app error logs; if they match 1:1, each inference request is failing upstream while the HTTP route may still return 200.
3. List candidate tuned endpoints in both dev and prod:
   - `gcloud ai endpoints list --project=dev-cola-rd --region=us-central1 --filter='displayName~passenger OR displayName~survey' --format='table(name.basename(),displayName,createTime)'`
   - `gcloud ai endpoints list --project=prod-cola-rd --region=us-central1 --filter='displayName~passenger OR displayName~survey' --format='table(name.basename(),displayName,createTime)'`
4. Describe the endpoint before choosing it:
   - `gcloud ai endpoints describe <ENDPOINT_ID> --project=<PROJECT> --region=us-central1 --format='yaml(name,displayName,deployedModels)'`
5. Compare the endpoint project number in `GEMINI_MODEL_ENDPOINT` with the Cloud Run runtime project. A prod service using a dev project-number endpoint is a strong cause of VPC-SC denial.

## Fix pattern

For production, set Cloud Run env vars explicitly and make the change durable in deployment config, not only a one-off manual Cloud Run edit:

```yaml
--set-env-vars: PROJECT_ID=prod-cola-rd,GEMINI_MODEL_PROJECT_ID=prod-cola-rd,GEMINI_MODEL_LOCATION=us-central1,GEMINI_MODEL_ENDPOINT=projects/586561863834/locations/us-central1/endpoints/901309813462401024
```

If using `cloudbuild.yaml`, add the three `GEMINI_MODEL_*` env vars to the deploy step so future deployments do not revert to code defaults.

Alternative fixes if production intentionally calls a dev endpoint:

- ask the GCP/VPC-SC owner to allow the prod service account and source perimeter to call `aiplatform.googleapis.com` in the dev project, or
- deploy/copy the tuned model endpoint into the prod project/perimeter.

## Reporting notes

Report separately:

- Cloud Run serving state (`Ready`, revision, traffic split, HTTP status)
- model-call failure state (403 VPC-SC from `aiplatform.googleapis.com`)
- data-quality impact (all labels become `False` due to fail-open fallback)
- exact prod endpoint resource to use
- whether the fix is a one-off Cloud Run env update or a repo/deployment-config change
