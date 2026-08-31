# passport-recog-data project intake

Session date: 2026-05-16
Repo: `colatour/passport-recog-data`
Local inspection path used in session: `/Users/wujinan/Documents/passport-recog-data`

## Product role

`passport-recog-data` is the data science team's passport recognition API. Colleagues use the company app to batch upload passport photos. This API recognizes structured passport fields; the company app handles staff confirmation/modification and downstream database write outside the data science team's ownership.

## Responsibility boundary

In scope for the data science team:
- Recognition API behavior
- Gemini / Vertex AI prompting and structured output
- Parsing, validation, error handling
- API performance, cost, concurrency, deployment config

Out of scope / owned elsewhere:
- Company app upload UI
- Staff confirmation UX
- Final database write
- Storage of passport images or recognition results

## Confidentiality constraint

Passport data is highly confidential. The API should not persist passport photos or recognition results. When inspecting or modifying the project, check for accidental sensitive output paths:
- file writes
- database writes
- full-result logs
- CLI test scripts that print real recognition results
- CI logs that could capture payloads or responses

In the inspected code, the main API did not persist results, but `test.py` prints full API responses and recognized fields; treat this as a privacy risk if real passport images are used in local or CI testing.

## Accuracy definition

The target is 99% whole-record accuracy: a record is correct only when all returned fields are correct. As of 2026-05-16, measured whole-record accuracy was about 89%.

Label source:
- Staff confirm in the company app => counted correct
- Staff modify any result => counted wrong

This is stricter than field-level accuracy. For 8 fields, high per-field accuracy can still produce much lower whole-record accuracy.

## Architecture observed

Key files:
- `app.py` — Flask API entrypoint; exposes `/api/passport/recognize`, `/api/passport/recognize/batch`, `/health`; wraps WSGI as ASGI via `WsgiToAsgi`.
- `src/passport_service.py` — decodes BASE64 image, invokes analyzer, parses results.
- `src/vision_analyzer.py` — Gemini / Vertex AI client; default model `gemini-2.5-flash`; sends field-level requests using `google-genai` with `genai.Client(vertexai=True)`.
- `src/prompt_templates.py` — prompts for individual passport fields.
- `src/result_parser.py` — JSON parsing and deterministic field validation.
- `cloudbuild.yaml` — Cloud Build build/push/deploy to Cloud Run.
- `Dockerfile` — Python 3.13 slim, Hypercorn ASGI startup.

Endpoints:
- `POST /api/passport/recognize`
- `POST /api/passport/recognize/batch`
- `GET /health`

Recognized fields:
- 中文名稱
- 英文名稱
- 國籍
- 護照號碼
- 性別
- 出生年月日
- 護照效期
- 身分證字號

Field-splitting strategy:
- The implementation does not crop the image into multiple images.
- It reuses the same image part and sends separate Gemini requests with field-specific prompts.
- One passport image can therefore trigger one Gemini request per recognized field.

## Deployment notes

No `.github/workflows` directory was present in the inspected repo. CI/CD appears to rely on external Cloud Build triggers that invoke root `cloudbuild.yaml` on branch pushes.

`cloudbuild.yaml` deploys to Cloud Run with notable settings:
- `--use-http2`
- `--timeout 900`
- `--concurrency 80`
- `--max-instances 1`
- `--min-instances 0`
- `--ingress internal-and-cloud-load-balancing`
- env vars: `PROJECT_ID`, `GEMINI_MAX_WORKERS=12`, `IMAGE_CONCURRENCY=3`, `BATCH_SIZE=100`

Branches:
- `main` represents production
- `develop` represents development
- Pushes to `main` deploy only production according to the user's description; exact trigger definitions are external to the repo.

## Inspection findings / pitfalls

- `README.md` said Python 3.14+, while `Dockerfile` used `python:3.13-slim`; keep docs aligned with runtime.
- `類別圖.md` omitted `PERSONAL_ID` in the enum despite code supporting it; diagrams can lag code.
- `app.py` and `vision_analyzer.py` require env vars at import time (`BATCH_SIZE`, `IMAGE_CONCURRENCY`, `GEMINI_MAX_WORKERS`), which can make local tests/IDE imports brittle.
- `test.py` prints full API responses and recognized fields; avoid real passport data in tests/logs.

## Accuracy-improvement framing

For moving from 89% whole-record accuracy toward 99%, do not rely only on prompt edits. Separate the work into three layers:

1. Gemini recognition layer
   - prompt changes
   - model choice
   - thinking budget
   - field splitting strategy

2. Deterministic validation / correction layer
   - MRZ checksum validation
   - Taiwanese ID checksum
   - sex vs ID second digit consistency
   - passport number vs MRZ check digit
   - birth/expiry date format and reasonableness
   - nationality consistency (`TWN`, `REPUBLIC OF CHINA`)
   - English name vs MRZ name consistency

3. Privacy-preserving feedback/evaluation layer
   - Store no photos and no recognized values
   - If metrics are needed, store only non-sensitive aggregates such as changed-field flags, error categories, confidence buckets, source (`visual`/`mrz`), latency, and model/prompt version.
