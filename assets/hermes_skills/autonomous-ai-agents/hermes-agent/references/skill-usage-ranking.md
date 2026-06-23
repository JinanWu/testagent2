# Skill usage ranking

Use this when you need to decide which skills are truly high-frequency or safe to prune.

## Primary sources

- `hermes curator status` — fast human-readable summary with most/least active skills.
- `~/.hermes/skills/.usage.json` — machine-readable per-skill telemetry.

## Fields to inspect

- `use_count` — best first-pass signal for frequency.
- `view_count` — useful for detecting skills that are inspected often but rarely invoked.
- `last_used_at` / `last_viewed_at` — recency signal.
- `patch_count` — shows maintenance pressure.

## Interpretation notes

- Treat `use_count` as the main ranking key, but normalize skill identity before comparing counts.
- Path-prefixed variants can represent the same conceptual skill under different namespaces (for example `skill-name`, `category/skill-name`, `category:skill-name`).
- When judging true frequency, dedupe these aliases first; otherwise the same concept may look artificially split across several entries.
- High `use_count` with recent `last_used_at` usually means keep.
- `use_count = 0` and `last_used_at = never` is a strong prune candidate if the skill is long or redundant.

## Handy one-liner

```bash
python3 - <<'PY'
import json, os
p=os.path.expanduser('~/.hermes/skills/.usage.json')
rows=[]
for name, info in json.load(open(p)).items():
    rows.append((info.get('use_count',0), info.get('last_used_at') or '', name))
for i, (use, last, name) in enumerate(sorted(rows, key=lambda x:(-x[0], x[1], x[2]))[:30], 1):
    print(f"{i:>2}. {name} — use={use} — last={last or 'never'}")
PY
```