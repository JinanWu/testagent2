# Script-only monitoring verification

Use this when a cron job reports `ok` but the user wants to know whether the underlying monitoring source was actually present.

## Verify in layers

1. **Cron execution**
   - Check `last_status`, `last_run_at`, and whether the job is enabled.
   - `ok` only means the job did not crash.

2. **Wrapper and real script**
   - Confirm the Hermes wrapper under `~/.hermes/scripts/` points to the real project script.
   - Inspect the real script’s data fetch path, not just the cron definition.

3. **Upstream data source**
   - Run the script or the upstream API call directly.
   - Confirm whether the source returns usable records, not just a HTTP 200 / JSON body.
   - For quote feeds, treat placeholder prices like `z='-'` or `pz='-'` as unusable for trigger logic.

4. **Business rule output**
   - Confirm whether the script would emit a notification or intentionally stay silent.
   - Empty stdout is a deliberate silent path for watchdog jobs, not necessarily a failure.

## What to report

- If the source is present and usable: say the data source is live.
- If the source returns only placeholders: say the feed is partial/unusable for trigger logic.
- If the job ran but stdout was empty: say the monitor was silent, not that it “succeeded with alerts”.

## Stock-watch example

The intraday stock monitor used TWSE MIS quotes. One symbol returned a record but with `z='-'` and `pz='-'`, so the parser skipped it as lacking a usable last price. That is the pattern to guard against: the feed exists, but the price field may not be usable for trigger decisions.
