# Bulk backlog creation from a reviewed plan

Use this when turning a reviewed project plan into many Apple Reminders items.

## Pattern
1. Create the target list first if it does not already exist.
   - In this session, creating the list with AppleScript was reliable:
     `osascript -e 'tell application "Reminders" to make new list with properties {name:"護照辨識壓力測試"}'`
2. Then use `osascript -l JavaScript` for idempotent bulk item creation/update.
   - Find the list by exact name.
   - For each item, search existing reminders by exact title.
   - Update if found; create if missing.
   - Set `name`, `body`, and `completed = false`.
3. Verify the write by reading back:
   - list total reminder count
   - unfinished count
   - first/last few titles

## Notes
- For complex project backlogs, keep reminder bodies in a fixed 4-part structure:
  - 任務背景
  - 要執行的內容
  - 預期產出
  - 驗收標準
- When the user asks for importance or effort labels, encode them in the title prefix so the list stays sortable, e.g. `[P0][SP3]`.
- If a first-pass JavaScript lookup fails on a missing list, create the list first and retry.
- For long project plans, detailed bodies are preferred over terse one-line reminders so the item remains executable later.
