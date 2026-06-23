# Passenger-survey Stage1 ETL readiness checks

Use this reference when asked whether passenger-survey / feedback-survey Stage1 can reliably transform source API rows into the BigQuery dashboard row shape.

## Readiness framing

Separate the answer into two axes:

1. **ETL/data-shape readiness** — source API, schema, serialization, MERGE safety, derived columns, BigQuery aggregate quality.
2. **Local/runtime readiness** — ADC/quota project, Vertex AI embedding access, BigQuery permissions, long-run durability on the user's machine.

Do not collapse a local credential/quota blocker into a claim that the ETL logic is broken. State precisely: e.g. "data transformation is structurally sound, but this local runtime cannot complete real Stage1 until Vertex quota-project permissions are fixed."

## Concrete Stage1 probe pattern

1. Identify Stage1 entrypoint and write semantics.
   - CLI: `python3 -m embedding_pipeline.cli --stage stage1 ...`
   - Implementation: `embedding_pipeline/orchestrator.py::run_stage1`
   - Verify it uses staging table + MERGE and protects Stage2/HM rows (`MATCHED` only updates when HM fields are all NULL).

2. Confirm live GCP context.
   - `gcloud config list` for account/project.
   - `gcloud auth application-default print-access-token >/dev/null` for ADC existence.
   - In Python, inspect `google.auth.default(...); creds.quota_project_id`.
   - If `quota_project_id` is `None`, first try `gcloud auth application-default set-quota-project <project>`.
   - If `quota_project_id` becomes set but Vertex still fails with `USER_PROJECT_DENIED`, treat it as a real IAM gap rather than an ADC-file problem: the authenticated ADC principal still lacks `serviceusage.services.use` on the quota project. Grant `roles/serviceusage.serviceUsageConsumer` (or an equivalent custom role) on the intended quota project and retry after propagation.
   - To verify this precisely when gcloud lacks a convenient subcommand, call Cloud Resource Manager `projects.testIamPermissions` for `serviceusage.services.use` using a gcloud access token; an empty `{}` response means the permission is absent.

3. Probe source API with a small bounded window before full execution.
   - Use 1-hour or 1-day production/API window, not a full month, to learn row count and sample fields.
   - Capture only 2–3 representative rows and field list.

4. Run a small Stage1 dry-run with the real pipeline.
   - Expected checkpoint sequence: API rows -> nickname extraction -> nickname replacement -> embedding -> UMAP -> serialization.
   - If real Vertex embedding is blocked by local ADC/quota state, do **not** write to BigQuery. For a data-shape-only probe, monkeypatch `generate_embeddings` with deterministic fake vectors and keep `dry_run=True`; this validates DataFrame/serialization/UMAP shape without claiming a full runtime pass.

5. Validate BigQuery target using aggregates, not dumps.
   - Schema includes `tour_name`, `embedding_vector`, x/y, HM/AI mark fields, Stage2 derived fields, `ingested_at`.
   - Key uniqueness: `COUNT(*) - COUNT(DISTINCT FORMAT('%d#%d', appoint_no, opinion_no))`.
   - Required quality: invalid `tour_date` format, empty embeddings, missing x/y, missing `tour_name`, null/blank sentiment fields.
   - Group by month/window to surface localized gaps.

6. Report with explicit pass/fail gates.
   - API availability and row count.
   - Dry-run or actual Stage1 command, runtime, row count, and failure point if any.
   - BigQuery row counts, duplicate key count, embedding/x-y coverage, tour_date validity, tour_name coverage.
   - 2–3 sample rows with only key/display fields and embedding dimension.

## Pitfalls

- Do not call fake-embedding dry-run a successful full Stage1 run; it proves shape, not Vertex/runtime readiness.
- Do not proceed to a real or dry-run Stage1 runtime validation until a one-row `generate_embeddings(['hello world'], ...)` smoke test succeeds with the same project/location/model; otherwise the run will fail at the embedding checkpoint and produce no new ETL evidence.
- Do not treat `quota_project_id` being present as proof that Vertex is usable. A set quota project can still fail with `USER_PROJECT_DENIED` if the ADC principal lacks `serviceusage.services.use`; verify with an embedding smoke test or `projects.testIamPermissions`.
- Do not treat ADC/quota setup failures as durable tool limitations. Capture the IAM/quota-project fix.
- Do not rely on `create_time` for Stage1/Stage3 date semantics when `ai-label` commonly returns it as NULL; prefer `tour_date` as the durable business date and report `create_time` null coverage separately.
- Do not assume `tour_name` is complete historically. Check coverage by month; dashboard display may need fallback/backfill even when Stage1 keys and embeddings are healthy.
- Do not dump large BigQuery tables; aggregate counts plus 2–3 samples are enough for readiness reporting.
