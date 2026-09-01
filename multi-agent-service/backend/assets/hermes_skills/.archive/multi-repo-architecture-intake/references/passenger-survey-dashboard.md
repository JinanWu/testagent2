# Passenger Survey / 意調表 Dashboard Architecture Notes

Use this reference when the user discusses the passenger survey / 意調表 dashboard stack. It captures durable repo roles and inspection cues from the 2026-05 dashboard intake. Treat repo README files as potentially stale; verify against code entrypoints, routes, config, Cloud Build, and BigQuery access code.

## Product framing

The 意調表 system has two dashboard directions:

1. PM dashboard
   - Audience: PMs / model-quality stakeholders.
   - Goal: compare AI labels with human/customer-service labels and judge whether AI labeling can reduce manual customer-service tagging work.
   - ETL dependency: `passenger-survey-dashboard-jobs` Stage 1 and Stage 2.

2. 心情指數 dashboard
   - Audience: product units.
   - Goal: quickly understand monthly traveler feedback, company/product reputation, tour-line satisfaction, and AI-generated travel-summary narratives.
   - ETL dependency: `passenger-survey-dashboard-jobs` Stage 3.
   - Implementation is temporarily hosted inside unrelated `multi-agent-*` projects because of company constraints; avoid treating all code in those repos as part of passenger survey.

Operational flow described by the user:
- Text enters the online system.
- It is sent to the passenger-survey ML API for AI labeling.
- Customer service reads, audits, and adjusts labels.
- Downstream complaint/other handling is outside the data science team’s control.
- `ai-label` and `label-analyze` are maintained by the backend engineer 申富.

## Repos and roles

- `colatour/passenger-survey-pred`
  - ML API for AI labels / sentiment.

- `colatour/passenger-survey-dashboard-jobs`
  - ETL. Stage 1/2 feed PM dashboard. Stage 3 feeds 心情指數 dashboard.

- `colatour/passenger-survey-dashboard`
  - PM dashboard.
  - Flask + Jinja + static JS; front/back are not separated.
  - Reads BigQuery directly into Pandas at process startup.
  - Key files: `app.py`, `data_loader.py`, `data_manager.py`, `templates/index.html`, `static/js/dashboard.js`, `cloudbuild.yaml`.

- `colatour/multi-agent-service`
  - 心情指數 dashboard backend is mounted as a sub-app inside an unrelated multi-agent FastAPI service.
  - Key passenger-survey files: `dashboard_backend/server.py`, `dashboard_backend/data_loader.py`, `dashboard_backend/data_manager.py`, `dashboard_backend/API_CONTRACT.md`.
  - Main service mount point is in `backend/app/main.py`: `app.mount("/dashboard", dashboard_app)`.

- `colatour/multi-agent-web`
  - 心情指數 dashboard frontend is hosted inside an unrelated multi-agent React/Vite frontend.
  - Key passenger-survey files: `frontend/src/components/dashboard/*`, `frontend/src/api/dashboard.ts`, `frontend/src/api/config.ts`, `frontend/nginx.conf`.

## PM dashboard inspection cues

Routes from `passenger-survey-dashboard/app.py`:
- `/dashboard`
- `/api/summary`
- `/api/gemini/strategy`
- `/api/heatmap`
- `/api/semantic`
- `/api/matrix`

BigQuery env vars:
- `GCP_PROJECT_ID`
- `GCP_DATASET_ID`
- `GCP_TABLE_ID`
- optional `EMBEDDING_COLUMN` defaults to `embedding_vector`

Important data semantics:
- `data_manager.py` auto-detects `ai_*_mark` and `hm_*_mark` columns and keeps labels where both sides exist.
- It computes Jaccard-style `consensus_score` and `is_exact` if absent.
- If available, it uses `semantic_outlier`, `hm_consensus_score`, `ai_consensus_score`, and `embedding_vector` for semantic-quality diagnostics/search.
- `data_loader.py` filters BigQuery rows to records with all HM mark columns non-null. Therefore PM dashboard stats describe the fully HM-labeled comparison subset, not necessarily all passenger survey records.

Cloud Build cues:
- Cloud Run service/image: `passenger-survey-dashboard`.
- Region seen in config: `asia-east1`.
- Build arg: `APP_ENV=production`.

Branch cue:
- PM dashboard has used `development` rather than `develop`; verify integration branch before editing.

## 心情指數 backend inspection cues

Dashboard backend routes are mounted under `/dashboard/api/v1/...`:
- `/dashboard/api/v1/hierarchy` deprecated.
- `/dashboard/api/v1/satisfaction/hierarchy` full hierarchy.
- `/dashboard/api/v1/satisfaction/nodes/{node_type}/{node_id}` node detail.
- `/dashboard/api/v1/satisfaction/nodes/{node_type}/{node_id}/children` paginated children.
- `/dashboard/api/v1/satisfaction/nodes/{node_type}/{node_id}/trend` trend.
- `/dashboard/api/v1/satisfaction/tours/{tour_id}` tour detail.
- `/dashboard/api/v1/opinions` opinion search.
- `/dashboard/api/v1/opinions/label-definitions` AI label definitions.

Default BigQuery settings seen in `dashboard_backend/data_loader.py`:
- `DASHBOARD_BQ_PROJECT=dev-cola-rd`
- `DASHBOARD_BQ_DATASET=passenger_survey_pred_dashboard`
- `DASHBOARD_BQ_TREE_TABLE=opinion_tree_metrics_summary_snapshot`
- `DASHBOARD_BQ_OPINION_TABLE=project_semantic_features`

Likely Stage 3 table roles:
- `opinion_tree_metrics_summary_snapshot`: tree metrics + summary snapshots (`metrics_tree`, `summary_tree`, `run_id`, `run_ts`, `tour_date_start`, `tour_date_end`).
- `project_semantic_features`: opinion-level rows used for search and AI label filters.

Tree hierarchy:
- root/company -> region -> line -> group -> product -> tour.
- Backend and frontend both model this drilldown path.

Metric mapping:
- `head_weighted_mean` maps to frontend `passenger` score.
- `level_weighted_mean` maps to frontend `route` score.
- `rev`, `revMom`, `revYoy` are present in the frontend contract but backend currently returns `None`; do not assume revenue-weighted satisfaction is implemented unless code/table confirms it.

## 心情指數 frontend inspection cues

Routes in React app:
- `/login`
- `/`
- `/runs/:runId`
- `/dashboard`
- `/dashboard/opinions`
- `/dashboard/tracking`

Dashboard API config:
- `APP_API_BASE_URL = /api/v1`
- `DASHBOARD_API_BASE_URL = /dashboard/api/v1`
- `VITE_API_BASE_URL` is normalized if it ends with `/api/v1` or `/dashboard/api/v1`.

Nginx cues:
- `/api/` and `/dashboard/api/` proxy to backend Cloud Run.
- `/dashboard` and `/dashboard/` return SPA `index.html`.

Auth caveat:
- Frontend wraps `/dashboard` in `ProtectedRoute`, sharing the multi-agent web auth state.
- `dashboard_backend/server.py` itself does not visibly add auth dependencies; verify Cloud Run/nginx/app auth before claiming backend APIs are protected.

## Pitfalls for future work

- Do not trust README alone; user explicitly warned dashboard docs may be stale. Read code first.
- Do not collapse the hosted multi-agent functionality with passenger-survey dashboard functionality; identify which files are the dashboard parasite/subsystem.
- Verify branch names per repo: `passenger-survey-dashboard` may use `development`, while `multi-agent-service` and `multi-agent-web` may use `develop`.
- For PM dashboard metrics, state the denominator/subset clearly: rows with complete HM labels, not necessarily all survey text.
- For 心情指數, distinguish tree snapshot data from opinion-search data.
