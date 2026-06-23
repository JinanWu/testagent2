# BigQuery CLI auth preflight

Use this before any `bq` query in a fresh or long-lived shell session.

Checklist:
1. Confirm the active account:
   - `gcloud auth list`
2. Verify the token can refresh non-interactively:
   - `gcloud auth print-access-token`
3. If refresh fails, re-authenticate interactively and then re-check the token:
   - `gcloud auth login --no-launch-browser`
4. If multiple accounts are configured, explicitly select the intended one before retrying `bq`:
   - `gcloud config set account <ACCOUNT>`
5. Re-run the BigQuery command only after the token check succeeds.

Safer inspection pattern:
- Prefer a two-step flow for schema/metadata:
  - `bq show --schema --format=prettyjson <table> > /tmp/table-schema.json`
  - inspect or parse the saved JSON separately
- This avoids brittle pipes and makes the failure point obvious.

Notes:
- Use this for CLI-based BigQuery work, especially when the shell session may have stale credentials or multiple accounts.
- The goal is to separate auth recovery from query debugging so access issues are diagnosed before chasing schema or SQL problems.
