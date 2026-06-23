# Cloud Run + GenAI triage

- Check readiness/traffic before assuming a runtime issue.
- Separate rollout state, serving limits, and upstream model behavior.
- Inspect request logs and stderr around the slow window.
- Long latency without quota errors often means fan-out, retries, or upstream slowness.
- Verify whether 429 / RESOURCE_EXHAUSTED indicates hard quota pressure or a broader failure pattern.
