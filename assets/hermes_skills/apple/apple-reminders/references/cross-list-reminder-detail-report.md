# Cross-list reminder discovery and detail report

Use this when the user asks to find a reminder/work item across all Reminders lists and then report it in detail.

Observed need from session:
- The target title may include a priority prefix such as `[P0]`.
- The user may say `各列表找到 ... 的工作項目`, which means scan every list, not just `提醒事項`.

Recommended workflow:
1. Search all lists, not one list.
2. Match both title and body/notes when looking for the item.
3. If the search phrase includes a prefix like `[P0]`, keep the prefix in the lookup, but if the exact match is missing, also try a normalized version without the leading priority tag.
4. Once found, report the list name first so the user can locate it quickly.
5. For a detailed report, include:
   - list name
   - exact title
   - completed status
   - due date
   - priority
   - body/notes snippet or full body if the user asked for detail
   - any obvious identifiers or context in the body
6. If multiple hits exist, show the closest match first and note the duplicates.

Pitfall:
- Do not silently narrow to the `提醒事項` list unless the user explicitly names that list.
- Do not return only the title; the user asked for a detailed report.
