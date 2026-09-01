# Passenger survey Vertex embedding + UMAP backfill pattern

Use this reference when a passenger-survey rebuild table has appended rows with empty `embedding_vector` and missing UMAP `x`/`y`, and the user accepts that newly generated coordinates are not comparable to the historical UMAP space.

## Safe sequence

1. Back up the target rebuild table first with `bq cp`.
2. Treat unavailable human labels explicitly:
   - for rows flagged `needs_hm_labels` where HM labels can no longer be recovered, set all `hm_*_mark` columns to `NULL`.
   - set `hm_consensus_score = NULL`, `hm_winner_labels = []`, `hm_winner_support = NULL`.
   - remove `needs_hm_labels` and add a durable provenance flag such as `hm_labels_unavailable`.
3. Do embedding recomputation via a staging table, then `MERGE` back to target.
   - Staging schema: `(appoint_no INT64, opinion_no INT64, embedding_vector ARRAY<FLOAT64>, embedded_at TIMESTAMP)`.
   - Anti-join target against staging so the job can resume safely after interruption.
   - Only merge rows where target `ARRAY_LENGTH(embedding_vector) = 0`.
4. Fit a new UMAP model only on the newly/backfilled embedding population if the user explicitly accepts non-comparability with old x/y.
   - Sample 30,000 rows from the missing-UMAP population when requested.
   - Persist the model locally with metadata: table, sample size, candidate rows, embedding dimensions, UMAP params, trained timestamp.
   - Transform every row still missing `x`/`y` with the saved model.
   - Write to an `xy` staging table, then merge back only where target `x IS NULL OR y IS NULL`.
5. Verify with scalar counts:
   - total rows and distinct keys
   - missing embedding rows
   - missing UMAP rows
   - `needs_hm_labels` should be 0 after marking unavailable labels
   - `hm_labels_unavailable` count should match the intentionally nulled label population

## Vertex AI request pattern

If the Python Vertex SDK fails because local ADC has no quota project / `serviceusage.services.use`, call the Vertex REST endpoint with a gcloud access token and `x-goog-user-project` header:

```python
import subprocess, requests
project = "dev-cola-rd"
loc = "us-central1"
model = "gemini-embedding-001"
token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
url = f"https://{loc}-aiplatform.googleapis.com/v1/projects/{project}/locations/{loc}/publishers/google/models/{model}:predict"
payload = {"instances": [{"content": text, "task_type": "CLUSTERING"} for text in texts]}
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "x-goog-user-project": project,
}
r = requests.post(url, headers=headers, json=payload, timeout=240)
r.raise_for_status()
vectors = [p["embeddings"]["values"] for p in r.json()["predictions"]]
```

Use `task_type="CLUSTERING"` for downstream semantic clustering / UMAP.

## Throughput pattern

Single-threaded batches of 100 rows may be too slow for 100k+ rows. Probe accepted batch sizes before scaling up; in this session, 100/150/200/250 all succeeded. A practical faster setup was:

- batch size: 250 texts/request
- workers: 6 concurrent requests
- staging flush: about 2,000 rows
- observed speed: roughly 8k-9k rows/minute, versus ~900 rows/minute single-threaded

Keep idempotency by writing all results to staging first and performing one merge after the parallel request phase. If a slow process is already running, stop it deliberately before launching the faster parallel process to avoid duplicate request cost; the staging anti-join and target merge make partial progress reusable.

## BigQuery client quota-project pitfall

If `google-cloud-bigquery` starts failing with `USER_PROJECT_DENIED` because ADC has a quota project the user cannot use, construct credentials from the ADC file and strip the quota project rather than recording a false claim that BigQuery is unavailable:

```python
from pathlib import Path
from google.cloud import bigquery
from google.oauth2.credentials import Credentials

adc_path = Path.home() / ".config/gcloud/application_default_credentials.json"
creds = Credentials.from_authorized_user_file(str(adc_path), scopes=["https://www.googleapis.com/auth/cloud-platform"])
creds = creds.with_quota_project(None)
client = bigquery.Client(project="dev-cola-rd", credentials=creds)
```

## Reporting expectation

For long-running embedding/UMAP backfills, report the active process id, current phase, staging row counts, target missing counts, throughput, and ETA. Be explicit that target missing counts will not drop until the staging-to-target merge runs.