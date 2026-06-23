# BigQuery dashboard tree-readiness checks

Use when a dashboard builds hierarchical/tree metrics from BigQuery rows, especially when the hierarchy depends on business-code prefixes (for example `tour_code` prefix → region/product group → leaf).

## Core readiness question

Separate two questions:

1. **Can the tree be structurally built?** Required grouping/display fields are present and non-empty.
2. **Can the tree metrics be scored?** Required metric fields are present and usable, not just structurally available.

A prior passenger-survey stage3 check showed this distinction matters: older output could build a tree but had `root_scored_count = 0` and null weighted means; after sentiment backfill, the same class of tree could both build and score.

## Aggregate checks before saying "yes"

Run aggregate verification for the target window:

- total rows and distinct business keys
- duplicate key groups / extra duplicate rows
- missing grouping fields, for example `tour_code`, `tour_name`, `tour_date`
- missing display fields used in leaves, for example guide/leader names if required
- missing or unscored metric fields, for example sentiment label/score
- null label columns if tree filtering depends on AI/HM label flags

Prefer `COUNTIF` and grouped counts over raw dumps. Show only 2-3 representative rows when examples are needed.

## Prefix mapping coverage

If the tree uses a prefix mapping:

1. Extract the mapping keys from the configured mapping table/file or from an existing known-good tree artifact.
2. Query current data prefixes for the target window.
3. Report:
   - mapped row count
   - unmapped row count
   - unmapped prefix count
   - top unmapped prefixes with row counts and 1-2 representative tour/product names
4. Distinguish data problems from taxonomy gaps. A non-empty `tour_code` with a plausible `tour_name` is usually a mapping/taxonomy gap, not bad source data.

Do not silently drop unmapped prefixes unless the user has accepted that behavior. State whether the tree can be built with exclusions or requires taxonomy updates for full coverage.

## Reporting shape

Recommended answer format:

- overall verdict: `can build`, `can build with exclusions`, or `blocked`
- structural readiness: key counts, duplicate/null checks
- scoring readiness: scored/unscored counts and metric availability
- taxonomy coverage: mapped vs unmapped rows and prefixes
- impact: which roots/branches can be produced and what data would be excluded
- next action: add mapping rules, accept exclusion, or fix missing data

## Passenger-survey stage3 specifics

For passenger-survey-style stage3 trees:

- Tree input commonly groups by `tour_code` prefix and displays `tour_name` in opinions/leaves.
- Score checks should include normalized `ai_sentiment_label` in `positive/negative` and `ai_sentiment_score` not null.
- Existing stage3 JSON artifacts may contain `metrics_tree.children` as a dictionary keyed by tree names; traverse dictionary keys as path labels rather than expecting each node to store `name` or `label`.
- Existing tree artifacts can be used to infer prefix → path coverage by traversing leaf `opinions[].tour_code[:3]`.
- Important summary fields to compare: `root_opinion_count`, `root_scored_count`, `root_head_weighted_mean`, `root_level_weighted_mean`, plus any `removed_*` / `unmapped_*` counts.
- When checking current BigQuery data against an inferred mapping, use `bq --format=json --max_rows=10000` for grouped prefix queries so the CLI does not truncate to the default row limit.
