# Passenger survey sentiment / mood-index contract notes

Use when evaluating or changing the passenger survey sentiment model that feeds the mood-index dashboard. Trace the full downstream chain before proposing API fields or score semantics.

## Repos / files to trace

Producer ML API:
- `passenger-survey-pred/app.py`
- `passenger-survey-pred/sentiment_analyzer.py`
- `passenger-survey-pred/README.md`

Dashboard ETL / snapshot job:
- `passenger-survey-dashboard-jobs/embedding_pipeline/constants.py`
- `passenger-survey-dashboard-jobs/embedding_pipeline/serialization.py`
- `passenger-survey-dashboard-jobs/embedding_pipeline/orchestrator.py`
- `passenger-survey-dashboard-jobs/embedding_pipeline/bigquery.py`
- `passenger-survey-dashboard-jobs/api_response_format.md`

Dashboard backend:
- `multi-agent-service/dashboard_backend/data_loader.py`
- `multi-agent-service/dashboard_backend/data_manager.py`
- `multi-agent-service/dashboard_backend/API_CONTRACT.md`

Dashboard frontend:
- `multi-agent-web/frontend/src/api/dashboard.ts`
- `multi-agent-web/frontend/src/components/dashboard/Analytics.tsx`
- `multi-agent-web/frontend/src/components/dashboard/analyticsMetrics.ts`

## Durable finding from the 2026-06 inspection

The downstream dashboard does not consume new mood fields directly. It consumes a 0-100 score derived from existing sentiment fields:

1. ML / survey API exposes legacy fields:
   - `Sentiment_Label` / `Sentiment_Score` in `passenger-survey-pred` response.
   - Downstream API normalizes these as `ai_sentiment_label` / `ai_sentiment_score`.
2. ETL Stage 1 stores only `ai_sentiment_label` and `ai_sentiment_score` as sentiment extras in BigQuery.
3. ETL Stage 3 computes per-opinion satisfaction with the legacy formula:
   - `Positive` -> `ai_sentiment_score`
   - `Negative` -> `1 - ai_sentiment_score`
   - other labels -> unscored / `None`
4. Stage 3 aggregates these 0-1 values into `head_weighted_mean` and `level_weighted_mean` in `metrics_tree`.
5. Backend converts tree scores to dashboard points with `round(value * 100, 1)`.
6. Frontend displays only `metrics.passenger`, `metrics.route`, and `metrics.rev`; it does not inspect sentiment labels.

## Recommended compatibility algorithm

When replacing the sentiment model with a new LLM mood-index model while preserving the old data format, do **not** add `Mood_*` API fields as the first move. Instead use an internal LLM result plus a legacy adapter:

Internal result:
```json
{
  "sentiment_type": "positive | negative | neutral | mixed | unclear",
  "mood_index": 0,
  "positive_score": 0.0,
  "negative_score": 0.0,
  "neutral_score": 0.0,
  "mixed_score": 0.0,
  "subjectivity_score": 0.0,
  "confidence": 0.0,
  "explanation": ""
}
```

Legacy adapter:
```python
dashboard_score = mood_index / 100
if mood_index >= 50:
    Sentiment_Label = "Positive"
    Sentiment_Score = dashboard_score
else:
    Sentiment_Label = "Negative"
    Sentiment_Score = 1 - dashboard_score
```

This makes the existing downstream formula recover the intended dashboard score:
- `mood_index=86` -> `Positive, 0.86` -> dashboard `86`
- `mood_index=42` -> `Negative, 0.58` -> dashboard `42`
- `mood_index=50` -> `Positive, 0.50` -> dashboard `50`

For `neutral`, `mixed`, and `unclear`, encode the product meaning through `mood_index` rather than new labels:
- neutral / unclear -> near 50
- mixed positive-leaning -> slightly above 50
- mixed negative-leaning -> slightly below 50

## Important pitfalls

- Do not assume `Sentiment_Score` is only model confidence. In the dashboard chain it is effectively used as the ingredient for a satisfaction score.
- Do not output `Neutral`, `Mixed`, or `Unclear` in `ai_sentiment_label` unless all downstream repos are updated; current Stage 3 returns `None` for labels other than Positive/Negative.
- Do not add `Mood_*` fields without planning BigQuery schema, serialization, Stage 3 select columns, backend adapter, frontend types, and UI semantics.
- The frontend currently treats score `0` as missing in `analyticsMetrics.ts` (`number === 0`). If using the legacy adapter only, clamp `mood_index` to at least `1` or separately fix the frontend missing-score rule.
- Keep the producer API response schema stable unless the user explicitly wants downstream schema migration.

## Prompt guidance for this contract

The LLM prompt should emphasize that `mood_index` is the final dashboard mood score:
- 50 is neutral.
- >50 is positive, <50 is negative.
- mixed is not neutral, but still needs an overall leaning.
- neutral / unclear should stay close to 50.
- Judge the author’s expressed attitude, not the event’s objective business impact.
- Turnarounds such as `但 / 不過 / 然而 / 雖然` usually make the latter clause more important.
