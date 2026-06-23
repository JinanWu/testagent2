# Model cost/performance routing for Hermes

Use this when a user asks whether to run Hermes on the strongest model all the time or to switch models based on task difficulty.

## Durable workflow

1. Gather current model prices from a provider API when possible. For OpenRouter-compatible routing, `https://openrouter.ai/api/v1/models` exposes model IDs, context lengths, and pricing fields (`prompt`, `completion`, `input_cache_read`) as USD per token. Convert to USD / 1M tokens by multiplying by 1,000,000.
2. Gather benchmark scores from an independent leaderboard such as Artificial Analysis (`https://artificialanalysis.ai/models`). Its pages include JSON-LD datasets with Intelligence Index, speed, context window, and price fields that can be parsed from HTML.
3. Normalize prices with an explicit usage assumption. A reasonable default for agent work is 80% input / 20% output tokens, but state the assumption because agent workloads vary.
4. Compute:
   - blended_cost = input_price * input_share + output_price * output_share
   - value_score = benchmark_score / blended_cost
   - pairwise intelligence gain and cost multiple
5. Translate numbers into routing policy, not just a table. The practical recommendation usually matters more than the exact CP score.

## Example pattern from a GPT-5.5 / GPT-5.4 / GPT-5.4-mini comparison

Observed from OpenRouter model API and Artificial Analysis at the time of the session:

- GPT-5.5: Intelligence Index ~60.24; input $5.00 / 1M, output $30.00 / 1M
- GPT-5.4: Intelligence Index ~56.80; input $2.50 / 1M, output $15.00 / 1M
- GPT-5.4-mini: Intelligence Index ~48.90; input $0.75 / 1M, output $4.50 / 1M

Under an 80% input / 20% output assumption:

- GPT-5.5 blended cost: ~$10.00 / 1M; value score ~6.02
- GPT-5.4 blended cost: ~$5.00 / 1M; value score ~11.36
- GPT-5.4-mini blended cost: ~$1.50 / 1M; value score ~32.60

Interpretation: the strongest model may only add a modest benchmark gain while multiplying cost. Prefer a routing policy:

- default low-cost model for daily chat, summarization, simple file/terminal work, straightforward research
- mid-tier model for coding, debugging, API/schema reconnaissance, moderate multi-file reasoning
- top model for production-risk incidents, architecture decisions, hard root-cause debugging, final review, or when cheaper models get stuck

## Hermes-specific advice

- Use `/model` interactively to switch for a session.
- Use `hermes chat -m <model> -q "..."` for one-shot high-value tasks.
- If configuring a long-running workflow or cron job, pin the model explicitly in the job config when cost predictability matters.
- For subagents/delegation, consider assigning cheaper models to broad exploration and reserving the strongest model for synthesis/review.