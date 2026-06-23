# Context Compression Notes

This note captures the current runtime compression design in Hermes Agent.

## Two layers

1. Gateway session hygiene (`gateway/run.py`)
- Pre-agent safety net for long sessions.
- Roughly fires at 85% of model context length.
- Uses the latest API-reported token count when available; otherwise falls back to a rough estimate.

2. Agent `ContextCompressor` (`agent/context_compressor.py`)
- Primary in-loop compressor.
- Default threshold is 50% of context length (`compression.threshold = 0.50`).
- Operates on real token usage from the agent loop.

## Main algorithm

- Prune old, verbose tool outputs before summarization.
- Preserve the head of the conversation and a protected tail.
- Summarize the middle section with an auxiliary LLM call (`task="compression"`).
- Reinsert a structured summary message and keep recent messages intact.
- Preserve tool_call / tool_result pairing when trimming.
- On later compressions, update the previous summary instead of starting over.

## Model / provider resolution

- Runtime compression uses `auxiliary.compression.*` config.
- Default is `provider: auto`, `model: ""`.
- Auto selection can reuse the main provider/model first, then fall back through supported auxiliary providers (e.g. OpenRouter, Nous, custom endpoint, Anthropic-compatible/direct providers).
- If the configured summary model cannot fit the compressed content, Hermes warns at startup and may lower the live threshold for the session.
- The summary model must have at least the minimum context floor (64K) and ideally should be able to handle the main model's compression threshold.

## Important distinction

- `trajectory_compressor.py` is a separate offline dataset-processing tool and is not the same as runtime session compression.
- Its default summarization model is different (currently Gemini via OpenRouter).

## Useful config keys

- `compression.enabled`
- `compression.threshold`
- `compression.target_ratio`
- `compression.protect_last_n`
- `auxiliary.compression.provider`
- `auxiliary.compression.model`
- `auxiliary.compression.timeout`

## Common failure mode

- If the auxiliary summary model's context window is smaller than the amount Hermes needs to summarize, compression can degrade or fall back. The agent checks this at session start and warns early.