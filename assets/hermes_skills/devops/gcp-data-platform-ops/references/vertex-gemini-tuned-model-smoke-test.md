# Vertex AI Gemini tuned model discovery and smoke test

Use when the user needs to find Gemini fine-tuned models in a dev/prod GCP project and verify whether a selected model endpoint can be called before broader validation.

## Discovery pattern

1. Confirm active project and account:
   ```bash
   gcloud config get-value project
   gcloud auth list --filter=status:ACTIVE --format='value(account)'
   ```
2. List Vertex AI models in likely regions first, then widen if needed:
   ```bash
   gcloud ai models list --project=<PROJECT_ID> --region=us-central1 \
     --format='table(name,displayName,versionAliases,labels,createTime)' --limit=100
   ```
3. Identify Gemini tuned models by labels such as:
   - `google-vertex-llm-tuning-base-model-id` containing `gemini-*`
   - `google-vertex-llm-tuning-job-id`
   - `tune-type: sft`
4. For candidates, describe the model to capture deployed endpoints/checkpoints:
   ```bash
   gcloud ai models describe <MODEL_ID> --project=<PROJECT_ID> --region=<REGION> --format=json
   ```
5. Describe the endpoint before calling it:
   ```bash
   gcloud ai endpoints describe <ENDPOINT_ID> --project=<PROJECT_ID> --region=<REGION> --format=json
   ```
   Report `deployedModels`, `trafficSplit`, model resource, and checkpoint.

## Minimal Python call probe

Known-good pattern using the `google-genai` SDK against a deployed Vertex AI endpoint:

```python
from google import genai

PROJECT_ID = "dev-cola-rd"
LOCATION = "us-central1"
MODEL_ENDPOINT = "projects/<PROJECT_NUMBER>/locations/<LOCATION>/endpoints/<ENDPOINT_ID>"

client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
response = client.models.generate_content(
    model=MODEL_ENDPOINT,
    contents="導遊很親切，行程安排順暢，餐食也很好吃。",
)
print(response.text)
```

For classifier-style tuned Gemini models that return JSON labels, add a `json.loads(response.text)` check and report:
- `call_status`
- elapsed seconds
- whether response JSON is valid
- number of returned keys
- 2-3 notable true labels, not the entire payload unless needed

## Reporting shape

Keep the discovery report compact:
- project/account
- scanned scope/regions
- candidate model display name + model ID
- base model and tuning job ID
- deployed endpoint + deployed model ID + checkpoint
- create time in local timezone if useful

For smoke tests, report exact prompt, success/failure, latency, JSON validity, and key label outcomes.

## Pitfalls

- Do not start broad functional validation until the user has selected the exact candidate model.
- Do not assume a model resource is callable directly; for deployed tuned models, use the endpoint resource shown in `deployedModels`.
- `global` may appear in the locations list but model listing can return 404 for Vertex AI model endpoints; treat that as an endpoint/region API limitation, not proof there are no models elsewhere.
- An ADC warning about missing quota project may appear with end-user gcloud credentials; if the call succeeds, note the warning but do not treat it as a blocker.
