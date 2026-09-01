# Hierarchy routing via `tour_code`

Session pattern: the dashboard hierarchy/route/line/region classification was not inferred from the opinion text. It was driven by a dedicated `tour_code` prefix mapping in the pipeline.

## What to check first

1. Find the mapping table.
   - Search for a `tour_code` → leaf lookup or a prefix-to-leaf dict.
   - In this project, the key function was `_resolve_tour_code_to_leaf_key(tour_code)`.

2. Verify prefix matching behavior.
   - Longest-prefix first is important when 3-char codes can collide with 2-char codes.
   - Example risk: `SKP` must not be swallowed by `SK`; `WXP`/`WXJ`/`WXM` similarly need 3-char precedence.

3. Confirm the tree assembly step.
   - A fixed `organizational_level_dict` receives rows via the resolved leaf key.
   - Rows that fail mapping go to `unmapped_rows`; inspect the unmapped count before blaming labels or UI rendering.

4. Distinguish structure from content.
   - The hierarchy can be intentionally sparse.
   - If a branch is missing children, confirm whether the upstream tree is sparse by design before treating it as a bug.

## Useful debug questions

- Is the issue in the mapping table, the prefix resolver, or the downstream tree layout?
- Does the code use exact-name matching, prefix matching, or a custom remap table?
- Are there rows in `unmapped_rows`, and do they cluster on a specific prefix family?

## Why this matters

For this dashboard family, the stable routing key is often `tour_code`. When users say “this line/route/category looks wrong,” the first hypothesis should be the `tour_code` prefix map, not the displayed label text.
