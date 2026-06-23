# Mood-index sentiment scoring notes

Context: user was designing a new model for a 心情指數儀表板. The discussion focused on defining emotion scores for short text where a passage may contain both positive and negative sentiment, or may have no clear sentiment at all.

## Recommended product definition

Use a multi-field result, not just one positive/negative number.

Core fields:

- `sentiment_type`: `positive | negative | neutral | mixed | unclear`
- `mood_index`: 0-100 compact display score; 50 is neutral baseline
- `positive_score`: 0-1
- `negative_score`: 0-1
- `neutral_score`: 0-1
- `mixed_score`: 0-1
- `subjectivity_score`: 0-1
- `confidence`: 0-1
- `main_emotions`: optional emotion labels
- `evidence`: text spans and reasons

Key distinction:

- `服務很好，但等待時間太久` may have `mood_index` near 50, but it is mixed, not neutral.
- `今天系統新增三個功能` may also be near 50, but it is objective/neutral with low subjectivity.

## Scoring guidance

Suggested starting formula:

```text
valence_score = positive_score - negative_score
mood_index = 50 + 50 * valence_score * confidence_adjustment
```

However, `mood_index` should be treated as a display aggregation. Store component scores to preserve meaning.

## LLM suitability

This task is suitable for an LLM as the first implementation because it requires:

- understanding mixed sentiment;
- distinguishing objective neutral text from emotional neutrality;
- respecting a chosen target, e.g. author sentiment vs event impact;
- outputting explanations/evidence spans for audit;
- handling short, ambiguous, or domain-dependent text.

For the user's dashboard, a clean first definition is: judge the author's expressed subjective emotion/evaluation, not the event's objective good/bad impact, unless the product explicitly changes that scope.

## Fine-tuning judgment

Do not start with fine-tuning while the scoring definition is still unsettled. The immediate bottleneck is the label/schema definition, not model capacity.

Fine-tuning becomes worth considering only after:

1. definitions are stable;
2. a representative human-labeled eval set exists;
3. prompt-only LLM has a measured bottleneck;
4. cost/latency/throughput is material;
5. the team has stable error modes that training examples can fix.

Recommended evolution:

1. LLM prompt with strict JSON schema.
2. Human-labeled eval set of around 200-500 representative real examples.
3. Prompt/model comparison against metrics.
4. If volume grows, train/distill a smaller classifier for easy/high-confidence cases.
5. Keep LLM fallback for low-confidence, mixed, unclear, and audit samples.

## Reference concepts found during web lookup

- Sentiment analysis commonly includes polarity at document/sentence/aspect level and may go beyond polarity into emotion categories.
- VADER-style sentiment output keeps positive, negative, neutral, and compound scores; useful lesson: retain components, not only compound.
- Google GoEmotions uses fine-grained categories and includes positive, negative, ambiguous, and neutral emotions; useful lesson: real emotion labels are more nuanced than basic positive/negative.
- OpenAI model optimization guidance emphasizes an eval/prompt/fine-tuning loop: build evals, prompt effectively, then fine-tune only when desirable for task consistency, cost, latency, or scale.
- Hugging Face SetFit is a possible later-stage route for few-shot/small-model classification; useful when labeled examples are limited and inference cost/latency matters.

## Example cases

```text
這次活動很成功，大家都很滿意
=> positive, high subjectivity, high mood_index

服務很好，但等太久
=> mixed, both positive and negative evidence, mood_index maybe around 50-60 but mixed_score high

今天發布新版系統
=> neutral/objective, low subjectivity, mood_index around 50

雖然過程辛苦，但結果值得
=> mixed but positive leaning; contrastive ending carries more weight
```
