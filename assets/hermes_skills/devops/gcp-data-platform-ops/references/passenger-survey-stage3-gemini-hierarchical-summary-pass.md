# Passenger-survey Stage3 Gemini hierarchical summary pass

Use when a passenger-survey dashboard snapshot already has a usable Stage3 `metrics_tree`, but the user wants the `summary_tree` to be semantically useful at every hierarchy level rather than an empty tree or purely deterministic extractive placeholder.

## Durable pattern

1. Separate structure from semantic quality.
   - First prove that the `metrics_tree` is structurally correct and the dashboard can drill down.
   - Only then run the expensive Gemini semantic-quality pass.

2. Build summaries bottom-up.
   - Leaf / tour-level summaries summarize the actual survey comments for that node.
   - Parent summaries summarize their child summaries, not arbitrary unrelated rows.
   - This preserves the intended reading path: tour/product -> group/line -> region/root, so each manager can read the rollup for their own scope.

3. Use a checkpoint cache for large trees.
   - Cache each completed node by stable node path/key.
   - Write the cache incrementally so interruptions can resume without redoing successful model calls.
   - Report cache progress as `completed_nodes / total_nodes`.

4. Prefer a model-call transport with per-request timeout for long runs.
   - If SDK/gRPC calls can hang, use Vertex Gemini REST calls with explicit request timeout.
   - Keep the model name, project, region, and timeout visible in the run notes.

5. Protect local MacBook runs and keep exactly one cache writer.
   - Wrap the long driver in `caffeinate -dimsu ...` or equivalent.
   - Verify `pmset -g assertions` and report the caffeinate pid plus the Python/background pid.
   - If a previous Hermes background process reports `exit -15`/SIGTERM, do not assume all descendants died. Before relaunching a checkpointed Gemini pass, check for orphan `python ...build_may_2026_gemini_summary_tree...` and `caffeinate` processes still running, then stop superseded ones so two processes do not write the same cache file concurrently.
   - Report old killed PIDs separately from the active replacement process/session.

6. Validate before writing BigQuery.
   - `metrics_tree` node count equals `summary_tree` node count.
   - Every `children` key is mirrored recursively between metrics and summary trees.
   - Known important drilldown paths exist (for this project, include representative region/group/product/tour-code paths such as 美加紐澳 -> 紐澳組 -> 三城全覽 -> product/tour_name -> tour_code when applicable).
   - Root summary is non-empty.
   - Only append or replace the formal BigQuery snapshot after validation passes.

## Reporting format for the user

For progress/status updates, keep the first sentence tied to the user's active objective (for example: "this is the Stage3 Gemini May summary replacement run") before explaining pipeline context. If the user asks what Stage1/Stage2/Stage3 mean during an active repair, state which stage is actually in scope first, then summarize the other stages only as upstream/downstream context.

For progress/status updates, include:

- background session id and OS process ids
- exact command and working directory
- source table, target table, date range/window, and summary model
- source rows, mapped rows, unmapped rows, total tree nodes
- current cache completed node count, percent, remaining nodes, cache path, and cache mtime
- current tree path/region being summarized when available
- whether BigQuery has been written yet
- validation gates still pending
- if the user asks why it is slow, explain the actual execution order and bottleneck: cached nodes complete quickly, uncached nodes require real Gemini calls, parent nodes must wait for child summaries, higher-level rollups have longer prompts, and CPU near 0 usually means the driver is waiting on Vertex/Gemini rather than local compute

## Pitfalls

- Do not call a deterministic extractive tree a completed semantic summary pass unless the user explicitly accepted that fallback.
- Do not summarize every parent from raw global rows; doing so breaks the manager-level rollup semantics.
- Do not write the snapshot before recursive shape validation; a valid JSON object can still be unusable if child keys diverge from `metrics_tree`.
- Do not leave a long local Gemini pass vulnerable to Mac sleep or an unbounded hung request.