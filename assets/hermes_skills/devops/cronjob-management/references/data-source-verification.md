# Data-source verification for script-only monitoring jobs

This note captures a recurring pitfall observed when validating recurring monitors:

- A cron run returning `ok` only proves the script exited successfully.
- It does **not** guarantee that the job had fresh upstream data or that any alert condition was evaluated.
- For jobs that depend on external market/data APIs, verify the upstream source directly before declaring success.

Recommended verification pattern:

1. Inspect the cron definition to find the wrapper script and the underlying project CLI.
2. Read the job code to identify which data sources are required for a real alert.
3. Run a direct probe against the data source(s) in the same time window the cron would use.
4. Confirm the probe returns at least one current record for the monitored universe.
5. If the probe returns partial or missing coverage, mention that explicitly instead of calling the job healthy.

For the stock-watch intraday monitor, the useful distinction is:

- `job last_status = ok` => the script ran.
- `quote_count > 0` and `quote.date == today` => there was live intraday data.
- `quote_count = 0` or stale dates => the monitor may be silent or partially blind even though cron says ok.
