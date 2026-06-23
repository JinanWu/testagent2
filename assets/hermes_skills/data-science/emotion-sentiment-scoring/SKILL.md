---
name: emotion-sentiment-scoring
description: Design and evaluate emotion/sentiment scoring systems for short text, including mixed/neutral cases, LLM prompting, evals, and model/fine-tuning tradeoffs.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [any]
metadata:
  hermes:
    tags: [sentiment-analysis, emotion-classification, llm-evaluation, model-selection, text-classification]
---

# Emotion & Sentiment Scoring

Use this skill when the user asks how to define, implement, evaluate, or choose models for an emotion/sentiment/mood-index system, especially for short text where sentiment may be mixed, neutral, unclear, or domain-dependent.

## Core principle

Do not collapse the task into a single positive/negative score too early. A single score cannot distinguish:

- true neutral/objective text: `今天系統發布新版。`
- mixed sentiment: `功能很好，但速度太慢。`
- unclear/low-context text: `還可以。`
- author sentiment vs event impact: `公司裁員 200 人。`

Start by defining the target of judgment:

1. 作者表達出的主觀情緒 / 評價
2. 文章語氣的正負向
3. 事件本身對指定對象的好壞
4. 社群或留言整體氛圍

For the user's mood-index dashboard, prefer first defining the task as: 判斷「文本作者表達出的主觀情緒與評價」，不要推測事件本身好壞 unless the user explicitly changes the target.

## Recommended first-version output schema

Use structured output rather than a bare label:

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
  "main_emotions": [],
  "evidence": [
    {
      "text_span": "...",
      "polarity": "positive | negative | neutral",
      "reason": "..."
    }
  ],
  "explanation": "簡短說明"
}
```

Suggested mood-index interpretation:

- `50` = neutral baseline
- `>50` = more positive valence
- `<50` = more negative valence
- `mixed_score` and `neutral_score` must be shown or stored separately so 50-ish mixed text is not confused with objective neutral text.

A simple starting formula:

```text
valence_score = positive_score - negative_score      # -1 to 1
mood_index = 50 + 50 * valence_score * confidence_adjustment
```

Do not treat this formula as the whole product definition; it is only the compact display score. Store the component scores.

## Handling common edge cases

1. Mixed sentiment
   - If positive and negative evidence are both meaningful, set `sentiment_type = mixed` even when `mood_index` is near 50.
   - Keep evidence spans for each side.

2. Objective or no clear sentiment
   - If text lacks explicit subjective evaluation/emotion, prefer `neutral_score` high and `subjectivity_score` low.
   - Do not infer sentiment from event impact unless the task definition says to.

3. Contrastive conjunctions
   - Words such as `但`, `但是`, `不過`, `然而`, `雖然` often make the later clause carry the main conclusion.
   - Example: `東西不錯，但是價格太高` should usually lean negative while remaining mixed.

4. Short or context-poor text
   - Reduce `confidence` rather than forcing a strong label.
   - Use `unclear` when the text cannot be judged from available context.

5. Aspect-based cases
   - Split into sentiment units/aspects before aggregating.
   - Example: `服務很好，但等待時間太久` -> service positive, wait time negative, overall mixed.

## LLM vs fine-tuning guidance

Default recommendation: use LLM prompting + structured JSON first; do not start with LLM fine-tuning while the scoring definition is still moving.

Use LLM first when:

- labels or score definitions are still being designed;
- mixed/neutral/unclear distinctions matter;
- explanations and evidence spans are useful for PM/user trust;
- the team needs to build an evaluation set and annotation guideline;
- input text is short, nuanced, or domain-dependent.

Consider fine-tuning or a smaller classifier only after:

- definitions and schema are stable;
- there is a representative human-labeled eval set;
- prompt-only quality has a measured bottleneck;
- cost/latency/throughput pressure is material;
- there are stable error modes that training data can address.

Prefer this evolution path:

1. LLM structured-output prompt.
2. Build a 200-500 item human-labeled eval set from real data.
3. Iterate prompt/schema and measure accuracy/error types.
4. If volume grows, train or distill a smaller model for high-confidence cases.
5. Keep LLM as fallback for low-confidence, mixed, unclear, and audit samples.

Fine-tuning a large LLM is usually a later optimization, not the first move. A hybrid architecture is often more cost-effective: small model for easy/high-confidence cases, LLM for difficult/ambiguous cases.

## Evaluation checklist

Build evaluation before comparing models. Track at least:

- `sentiment_type` accuracy
- mixed precision/recall
- neutral precision/recall
- unclear rate and whether it is appropriate
- `mood_index` mean absolute error vs human score
- calibration of confidence
- performance by text length/source/domain
- disagreements between author emotion vs event impact
- examples with contrastive conjunctions and aspect-level sentiment

## Research and session notes

See `references/mood-index-model-selection.md` for condensed notes from a session discussing mood-index scoring, LLM suitability, fine-tuning tradeoffs, GoEmotions, VADER, OpenAI model optimization guidance, and SetFit.

## Pitfalls

- Do not report a 50-ish mood score as simply `neutral` without checking `mixed_score` and `subjectivity_score`.
- Do not fine-tune before the annotation rulebook is stable.
- Do not train on LLM-generated labels without human audit if the result will become a production truth source.
- Do not optimize cost before the team knows what correctness means.
- Do not hide evidence spans; they are useful for debugging and stakeholder trust.
