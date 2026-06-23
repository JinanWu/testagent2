# Topic search across Apple Reminders lists

Use this when the user asks whether a reminder list exists or wants to find reminders by topic/title across all lists, not just the main `提醒事項` list.

Recommended read-only probe (JXA / osascript):
1. Enumerate all lists.
2. Search every reminder's title and body for topic keywords.
3. Preserve the list name, reminder title, completed state, and due date if present.
4. If the user gave an exact list name, first try exact-name lookup; if missing, return available list names instead of guessing.

Useful output shape:
- list
- title
- completed
- due date (ISO string or null)
- body snippet if useful

Practical notes:
- For main-list inventory requests, keep using exact-name lookup of `提醒事項` and filter to unfinished reminders.
- For topic discovery, search all lists; a matching list name is often more useful than only matching reminder titles.
- Keep the result concise and operational; do not paste a full raw dump unless the user explicitly wants it.

Session example:
- A dedicated list named `護照辨識壓力測試` existed, found by scanning all list names.
