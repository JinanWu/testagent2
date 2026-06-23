# Billing export diagnostics for Gemini / Vertex AI cost questions

Session notes distilled into a reusable checklist.

## Goal
When a user asks for prod GCP costs related to Gemini/Vertex AI, identify the billing export source first and avoid assuming the active gcloud project is also the billing-export project.

## Durable workflow
1. Determine which Google account is active and whether it has project and billing export access.
2. Identify the billing account tied to the target GCP project.
3. Find the BigQuery billing export dataset/table and its location.
4. Run cost queries from a project where you have `bigquery.jobs.create`, but read from the export table in the billing dataset.
5. Filter by target project id, month window, and Gemini/Vertex AI related service/SKU/resource fields.
6. If Gemini-specific rows are empty, verify whether the export uses different service/SKU naming than expected before concluding there is no spend.

## Useful query patterns
- Total monthly spend by project:
  - `SELECT project.id, ROUND(SUM(cost), 6) ... GROUP BY project.id`
- Candidate Gemini spend scan:
  - filter on `LOWER(service.description)`, `LOWER(sku.description)`, and `LOWER(resource.name)` with `LIKE '%gemini%'`, `'%vertex ai%'`, and `'%generative ai%'`

## Pitfalls observed
- The BigQuery job project and the billing-export dataset project can be different; `bq query --project_id=<job-project>` may need to be set explicitly.
- Dataset location matters. If you query without the right `--location`, BigQuery may report the dataset as not found in the wrong region.
- You may be able to list datasets in a project but still lack table-level or billing-account permissions.
- When `bq ls --dataset_id=...` fails, `INFORMATION_SCHEMA.SCHEMATA` can still help confirm dataset location and existence in a region you can query.
- Billing export naming varies by billing account; verify the exact table name before querying.

## Verification checklist
- Confirm target project id.
- Confirm billing export dataset/table name.
- Confirm dataset location.
- Confirm query job runs from an authorized project.
- Check the month total, then the filtered Gemini/Vertex AI subset.
