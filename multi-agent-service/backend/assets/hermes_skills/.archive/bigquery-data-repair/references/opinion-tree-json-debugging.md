# Opinion tree JSON debugging notes

Session takeaway:
- The BigQuery snapshot table `dev-cola-rd.passenger_survey_pred_dashboard.opinion_tree_metrics_summary_snapshot` stores a precomputed dashboard snapshot, not raw rows.
- The exported JSON structure for one month can have top-level business nodes in `regions`, then nested `lines`, then nested `groups`.
- A misleading UI label like `未命名` is not necessarily present in the source JSON. It can appear when the frontend iterates the wrong level of a nested object.

Observed shape in this session:
- `regions[*]` contained real business nodes such as `中國`, `國內`, `日本`, `韓國`.
- Some `lines[*].groups[*]` entries were true child nodes like `未分類團控`.
- Some `lines[*].groups[*]` entries were internal metadata fields such as `head_weighted_mean`, `kind`, `level_weighted_mean`, `opinion_count`, `opinions`, `scored_count`.
- That means a frontend that blindly renders every item in `groups` as a child node may display bogus or unnamed nodes.

Useful checks:
- Compare the array/object level the UI renders against the intended business hierarchy.
- Count how many nodes have only metadata-like group entries versus real child nodes.
- Inspect the raw JSON keys before assuming the data lost its label.

Recommended workflow:
1. Export a pretty JSON sample to Documents for human inspection.
2. Reduce the structure to only business nodes before debugging label text.
3. Verify both backend shape and frontend rendering path before changing source data.
