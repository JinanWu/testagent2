# Vertex Gemini tuned model migration pattern

Use this when a Cloud Run / Flask prediction repo is being switched from local model inference or embeddings to a Vertex AI Gemini tuned model endpoint.

## Discovery

1. Confirm the active dev project and account:
   - `gcloud config get-value project`
   - `gcloud auth list --filter=status:ACTIVE --format='value(account)'`
2. List tuned Gemini models across Vertex AI regions, not only the expected app region. Tuned Gemini models may live in `us-central1` while the app or embedding calls use `asia-east1`.
3. Identify the model by labels:
   - `google-vertex-llm-tuning-base-model-id`
   - `google-vertex-llm-tuning-job-id`
   - `tune-type=sft`
4. Describe the model and endpoint before coding:
   - model resource name
   - endpoint resource name
   - deployed model id
   - checkpoint id
   - traffic split

## Minimal direct call

Use the `google-genai` SDK with the endpoint resource as `model`:

```python
from google import genai

client = genai.Client(vertexai=True, project="dev-cola-rd", location="us-central1")
response = client.models.generate_content(
    model="projects/<project-number>/locations/us-central1/endpoints/<endpoint-id>",
    contents="飯店房間很髒，導遊處理態度很差，希望公司改善。",
)
print(response.text)
```

Accept the ADC quota-project warning as a warning only if the call succeeds; do not treat it as a blocker.

## Repo migration shape

- Put endpoint settings behind environment variables with safe defaults:
  - `GEMINI_MODEL_PROJECT_ID`
  - `GEMINI_MODEL_LOCATION`
  - `GEMINI_MODEL_ENDPOINT`
- Change the prediction function to accept raw review text rather than an embedding vector if the tuned model does classification directly.
- Parse model output as JSON, but allow both pure JSON and fenced Markdown JSON. Reject missing/non-JSON responses explicitly.
- Normalize output to the API contract: build every expected label key and default missing labels to `False`; warn and ignore unknown labels.
- If keeping old embedding / PyTorch code for rollback or comparison, do not load it at app startup on the tuned-model branch. Lazy-load embeddings only if a retained helper is called.
- Update the response schema if the tuned model returns additional labels, for example severe complaint fields.

## Verification

1. `python3 -m py_compile app.py`
2. Direct endpoint call using the exact endpoint resource.
3. Function-level smoke test of the new `predict_label(text)` path.
4. Route-level Flask test-client smoke test with Pub/Sub push payload shape.
   - Set `SKIP_PUBSUB_PUBLISH=1` to avoid publishing during local tests.
   - If importing the app would download or load a heavy sentiment model, inject a small stub module for `sentiment_analyzer` in the smoke test. This verifies routing/model-call integration without paying the HF setup cost.
5. Report branch, commit, changed files, endpoint, request count, key labels, HTTP status, and any warnings.

## Pitfalls

- Do not assume model region matches Cloud Run/embedding region.
- Do not leave a local model loader running at import time if the branch is meant to validate a remote tuned model; it can mask the migration by failing before the endpoint is called.
- Do not index model output by list position after switching to JSON labels; use label names.
- Do not publish Pub/Sub messages during local smoke tests unless the user explicitly asked for an integration write.
