# AI newsletter collection pipeline notes

Use this pattern when Hermes is acting as a collection/editor agent for a recurring internal newsletter.

## Recommended workflow

1. Collect
- Run on a fixed schedule with cron.
- Pull from a curated source pool rather than doing ad hoc web searching each time.
- Prefer official sources first, then technical blogs, then application examples, then internal notes.
- Capture title, URL, source type, timestamp, and a short machine summary.

2. Store
- Do not store per-issue content in memory.
- Keep ephemeral content in a file tree so the user can review it later.
- Suggested layout:
  - `raw/` for fetched items
  - `shortlist/` for filtered candidates
  - `draft/` for generated copy
  - `final/` for user-approved output
- Markdown folders are the best starting point; a database can come later.

3. Draft
- Generate a draft only from stored items.
- Keep the structure fixed so the user can compare issues over time.
- Attach source links for every item.

4. Review
- The user is the final editor and reviewer.
- Hermes should not publish or treat a draft as final without explicit approval.
- The review surface should make it easy to delete, rewrite, or reorder items.

## Practical rules

- Daily output should stay short and high-signal.
- Weekly output should be a curated summary of the best items from the daily pool.
- Use stable source tiers:
  - official sources for the core signal
  - technical sources for deeper context
  - application sources for practical usage
  - internal sources for company-specific relevance
- Use cron for schedule, file tools for storage, and the model for summarization/editing.

## Pitfall

- Do not let the long-term memory store the changing article pool; memory is for stable preferences, rules, and durable project conventions only.
