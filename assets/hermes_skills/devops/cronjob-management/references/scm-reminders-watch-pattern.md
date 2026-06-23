# SCM stock pulse watch: target-list migration

Session note: the cron job `永豐投顧個股脈動提醒` was changed from writing to `提醒事項` to writing to `投資研究`.

## What changed
- Script constant `REMINDERS_LIST` changed to `投資研究`.
- AppleScript target list inside `add_reminder()` was updated to use the same list.
- Cron prompt wording was updated so the live job description matches the script.

## Verification
- Re-run syntax check after editing the Python file: `python3 -m py_compile /Users/wujinan/.hermes/scripts/scm_stock_pulse_watch.py`
- Check the cron job prompt preview after updating the prompt.

## Pitfall
If the cron prompt is updated but the script still points at the old Reminders list, the job will keep behaving like the old workflow even though the description looks correct.