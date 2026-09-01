#!/usr/bin/env python3
"""Summarize a Cloud Run batch log window for GenAI latency triage.

Input: a JSON array of Cloud Run log entries.
Output: a compact report with request counts, latency stats, and upstream gap clues.
"""

import json
import sys
from collections import Counter
from datetime import datetime, timezone


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def main() -> int:
    data = json.load(sys.stdin)
    reqs = [x for x in data if isinstance(x, dict) and 'httpRequest' in x]
    latencies = []
    for x in reqs:
        lat = x['httpRequest'].get('latency')
        if isinstance(lat, str) and lat.endswith('s'):
            try:
                latencies.append(float(lat[:-1]))
            except ValueError:
                pass

    print(f"entries={len(data)} requests={len(reqs)}")
    print(f"statuses={dict(Counter(str(x['httpRequest'].get('status')) for x in reqs))}")
    if latencies:
        latencies_sorted = sorted(latencies)
        p50 = latencies_sorted[len(latencies_sorted)//2]
        p95 = latencies_sorted[max(0, int(len(latencies_sorted)*0.95)-1)]
        print(f"latency_p50={p50:.3f}s latency_p95={p95:.3f}s max={max(latencies):.3f}s")
    for needle in ['RESOURCE_EXHAUSTED', 'quota exceeded', 'rate limit', 'ConnectError', 'INVALID_ARGUMENT', 'EOF']:
        hits = sum(needle.lower() in str(x).lower() for x in data)
        if hits:
            print(f"{needle}={hits}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
