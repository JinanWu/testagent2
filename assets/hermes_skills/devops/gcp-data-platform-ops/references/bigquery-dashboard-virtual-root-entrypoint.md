# BigQuery dashboard virtual root entrypoint pattern

Use when a BigQuery-backed dashboard has a formally correct tree, but the product/UI entrypoint taxonomy intentionally differs from the stored root taxonomy.

## Failure / request shape

- BigQuery `metrics_tree.children` contains a root category that is no longer the desired UI entrypoint label.
- A child under that root is the desired UI entrypoint.
- Another root category should remain a separate UI entrypoint.
- The frontend entrypoint list, map, or globe has hardcoded marker ids/names and resolves clicks by API node id.

Example shape:

```text
metrics_tree root
  歐洲郵輪
    歐洲
      東歐組
      南北歐組
      特殊歐組
      中西歐組
  國際郵輪
    美洲
    歐洲
    亞洲
    太洋洲
    非洲
    極地
```

Desired UI entrypoints:

```text
歐洲        -> source path 歐洲郵輪 / 歐洲
國際郵輪    -> source path 國際郵輪
```

Do not continue showing the old root `歐洲郵輪` entrypoint.

## Recommended repair

Prefer a precise adapter/frontend entrypoint alias, not a broad variable-depth tree rewrite, when the frontend/API contract is otherwise fixed-depth:

```text
regions -> lines -> groups -> products -> tours -> guests
```

Backend adapter pattern:

1. Build region entries as `(display_name, node, source_path)`, not just `(name, node)`.
2. For the known redundant root path:
   - display name: the desired UI label, e.g. `歐洲`
   - node: the child node, e.g. `metrics_tree.children['歐洲郵輪'].children['歐洲']`
   - source path: the original BigQuery path, e.g. `['歐洲郵輪', '歐洲']`
3. Skip the old redundant root entry, e.g. do not output display `歐洲郵輪`.
4. Leave unrelated root entries unchanged, e.g. `國際郵輪` remains `['國際郵輪']`.
5. Convert nodes using `source_path` so summary/trend lookups still use the stored tree path.

Frontend entrypoint pattern:

- Marker/list display name can be the alias (`歐洲`).
- Marker/list id must match the API node id produced from `source_path` (`歐洲郵輪 / 歐洲`), not the display label (`歐洲`), if the frontend resolves clicks by node id.
- Add a comment at the hardcoded marker/list definition explaining why display name and id differ.

## Why this is a special case, not a generalized tree fix

Use this approach when the hierarchy still has known business levels after the alias. Avoid generalizing every frontend drilldown to arbitrary child depth unless product requirements truly need variable-depth browsing.

A broad variable-depth rewrite can break assumptions such as:

- `region` children are rendered from `lines`
- `line` children are rendered from `groups`
- `group` children are rendered from `products`
- `product` children are rendered from `tours`
- `tour` children are rendered from `guests`

The alias keeps those levels stable while only changing the dashboard home entrypoint.

## Verification checklist

- API root `regions` includes the alias display node with id/path equal to the original source path.
- API root `regions` includes the separate root entry that should remain separate.
- API root `regions` does not include the old redundant root display name.
- The alias node's next children are the expected line/group labels immediately under the child node.
- Counts are preserved for the alias node and the separate root node.
- Summary/trend are non-empty or at least looked up using the source path, not the display label.
- Frontend build passes.
- Clicking the UI marker/list item resolves to the API node id, not just the display label.

## Reporting pattern

Report the decision explicitly:

- `歐洲` is a virtual/dashboard entrypoint backed by source path `歐洲郵輪 / 歐洲`.
- `國際郵輪` remains a real root entrypoint backed by source path `國際郵輪`.
- `歐洲郵輪` is intentionally hidden as a dashboard entrypoint.
- This is a narrowly-scoped alias to preserve the fixed API contract, not a generic taxonomy-depth change.
