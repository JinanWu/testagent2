# Cloud Build project-based Vertex/Gemini endpoint selection

Use this when a single `cloudbuild.yaml` deploys the same Cloud Run service class to dev/prod projects and the runtime must call the matching environment's Vertex AI / Gemini tuned endpoint.

## Failure shape

- Cloud Run service is Ready and request logs may still show HTTP 200.
- App logs show Vertex AI / Gemini call failures such as:
  - `403 PERMISSION_DENIED`
  - `SECURITY_POLICY_VIOLATED`
  - `VPC_SERVICE_CONTROLS`
  - `service: aiplatform.googleapis.com`
- A common root cause is a production Cloud Run service accidentally calling a development Vertex endpoint because app defaults point at dev and Cloud Run did not override `GEMINI_MODEL_*` env vars.
- If the application catches prediction errors and returns default labels, the data-plane failure can be silent: HTTP 200 with all-false/default model outputs.

## Preferred deployment pattern

When the user wants the repo-level deployment file to decide environment from existing Cloud Build variables, keep the logic inside `cloudbuild.yaml` and use built-in `${PROJECT_ID}` instead of requiring new trigger substitutions.

Use a bash deploy step rather than a flat `gcloud` args array:

```yaml
- name: 'gcr.io/google.com/cloudsdktool/cloud-sdk'
  entrypoint: 'bash'
  args:
  - '-c'
  - |
    set -euo pipefail

    if [ "${PROJECT_ID}" = "prod-cola-rd" ]; then
      GEMINI_MODEL_PROJECT_ID="prod-cola-rd"
      GEMINI_MODEL_LOCATION="us-central1"
      GEMINI_MODEL_ENDPOINT="projects/<prod-number>/locations/us-central1/endpoints/<prod-endpoint>"
    elif [ "${PROJECT_ID}" = "dev-cola-rd" ]; then
      GEMINI_MODEL_PROJECT_ID="dev-cola-rd"
      GEMINI_MODEL_LOCATION="us-central1"
      GEMINI_MODEL_ENDPOINT="projects/<dev-number>/locations/us-central1/endpoints/<dev-endpoint>"
    else
      echo "Unsupported PROJECT_ID for Gemini model endpoint: ${PROJECT_ID}" >&2
      exit 1
    fi

    gcloud run deploy '${_CONTAINERNAME}' \
      --image '${_CI_REGISTRY_IMAGE}/${PROJECT_ID}/${_CI_REGISTRY_IMAGE_NAME}/${_CONTAINERNAME}:${COMMIT_SHA}' \
      --region '${_ZONE}' \
      --set-env-vars "PROJECT_ID=${PROJECT_ID},GEMINI_MODEL_PROJECT_ID=$${GEMINI_MODEL_PROJECT_ID},GEMINI_MODEL_LOCATION=$${GEMINI_MODEL_LOCATION},GEMINI_MODEL_ENDPOINT=$${GEMINI_MODEL_ENDPOINT}"
```

Important quoting detail: use `$${GEMINI_MODEL_ENDPOINT}` for shell variables inside Cloud Build YAML so Cloud Build does not try to substitute them before bash runs.

## Scope discipline

- If the user asks to edit `cloudbuild.yaml`, do not update Cloud Build triggers or live Cloud Run settings unless explicitly asked.
- If you initially think trigger substitutions are cleaner, still follow the user's requested source of truth when they say the file should use project variables.
- Keep a fail-closed branch for unknown `PROJECT_ID` so a new environment cannot silently deploy with the wrong tuned model.

## Verification

- Parse YAML after editing.
- Check the deploy script contains both dev and prod endpoint resource names.
- Check no stale `_GEMINI_MODEL_*` trigger-substitution references remain if the design is `${PROJECT_ID}`-based.
- Review diff to confirm only the intended deployment file changed when scope is file-only.
