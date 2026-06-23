# API workflow notes

This reference captures the session-specific details that make the umbrella skill more actionable without bloating the main SKILL.md.

## Reconnaissance patterns

- Prefer the exact code path or embedded source used by the pipeline.
- Keep query windows narrow; the goal is a representative slice, not completeness.
- Sample 2–3 records and derive the union of keys from those samples.
- Compare endpoints by shared fields, unique fields, optional/null fields, and time fields.
- When the source is hierarchical, flatten recursively and carry ancestor labels down to leaf rows.
- Report URL, params, status code, record count, and field count.

## Burst-test patterns

- Confirm target host, environment, batch size, concurrency, timeout, and runtime cap before sending traffic.
- Resolve DNS first; if the hostname does not resolve, stop and report a DNS failure.
- Send a tiny baseline request before interpreting any saturation or timeout result.
- If an edge/CDN/WAF returns a challenge or 403 page, classify the run as edge-blocked rather than backend failure.
- Keep the path through the edge when the hypothesis is about edge timeout behavior; only bypass if the test objective is origin behavior.
- Save raw response artifacts, request metadata, and the smallest useful sample.
- Compare request-level latency/status with item-level success/failure so batch-level timeouts do not hide per-item failures.
- After a burst, run `/health` and a tiny probe again so dispatch starvation can be distinguished from upstream saturation.

## Reporting reminder

A good summary usually contains:

- endpoint and environment
- status / latency / count
- field count or item success rate
- one or two representative values
- the most likely bottleneck
- the next concrete check
