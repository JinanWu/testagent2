# Vertex Gemini tuned endpoint migration pattern

Use this when replacing an in-repo classifier with a Vertex AI Gemini tuned model endpoint, especially for passenger-survey style label classifiers.

## Discovery

1. Confirm active GCP project/account first:
   - `gcloud config get-value project`
   - `gcloud auth list --filter=status:ACTIVE --format='value(account)'`
2. List Vertex AI models by region. Gemini tuned models often appear as Vertex AI `models` with labels such as:
   - `google-vertex-llm-tuning-base-model-id`
   - `google-vertex-llm-tuning-job-id`
   - `tune-type=sft`
3. Describe the candidate model and endpoint before code changes:
   - model resource
   - endpoint resource
   - deployed model id
   - checkpoint id
   - traffic split

Observed passenger-survey dev target example:
- project: `dev-cola-rd`
- region: `us-central1`
- model display name: `passenger 2.0`
- model id: `6648087655940620288`
- endpoint: `projects/706707303745/locations/us-central1/endpoints/3740143283263766528`
- base model: `gemini-2_5-flash-lite`
- tune type: `sft`

## Minimal call probe

Use the `google-genai` SDK against the endpoint resource, not the base model name:

```python
from google import genai

client = genai.Client(vertexai=True, project="dev-cola-rd", location="us-central1")
response = client.models.generate_content(
    model="projects/706707303745/locations/us-central1/endpoints/3740143283263766528",
    contents="導遊很親切，行程安排順暢，餐食也很好吃。",
)
print(response.text)
```

A successful passenger-survey classifier call returns JSON-like label booleans. Validate that the response is parseable JSON and contains the expected label keys before wiring it into the service.

## Repo migration checklist

1. Verify repo, branch, and base before edits; for domanda repos use `develop` as PR base unless the user says otherwise.
2. Add endpoint configuration via environment variables so dev/prod can diverge safely:
   - `GEMINI_MODEL_PROJECT_ID`
   - `GEMINI_MODEL_LOCATION`
   - `GEMINI_MODEL_ENDPOINT`
3. Replace only the classifier call path first. Preserve old embedding/PyTorch code if useful for rollback/comparison, but avoid loading old local models on startup when the new endpoint is the active path.
4. Parse Gemini response robustly:
   - accept pure JSON and fenced ```json blocks
   - ignore unknown labels with a warning
   - default missing labels to `False`
   - fail closed to all-False only when the model call/parse fails
5. Keep API output schema compatible with downstream consumers. If the tuned model emits additional severe complaint labels, explicitly add output arrays for them only after confirming downstream acceptance.
6. Run verification:
   - Python compile/import check
   - minimal direct endpoint call
   - Flask test-client Pub/Sub payload smoke test with `SKIP_PUBSUB_PUBLISH=1` if available

## Pitfalls

- Do not pass the endpoint id to old `vertexai.language_models.TextGenerationModel` APIs; use `google.genai.Client(...).models.generate_content()` for Gemini endpoint calls.
- Do not leave both old local model loading and new endpoint inference active unless intentionally comparing outputs; startup can become slow or fail on unused local model dependencies.
- Do not silently drop new labels. Either add them to the response schema or document that they are intentionally ignored for compatibility.
- Treat ADC quota-project warnings as a warning, not a failed call, when the endpoint returns a real response.
