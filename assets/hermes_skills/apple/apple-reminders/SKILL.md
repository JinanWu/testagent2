---
name: apple-reminders
description: "Apple Reminders via remindctl: add, list, complete."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [Reminders, tasks, todo, macOS, Apple]
prerequisites:
  commands: [remindctl]
---

# Apple Reminders

Use Apple Reminders to manage personal tasks that sync across Apple devices via iCloud.

Current user environment note: `remindctl` is not installed on the user's Mac. Do not try `remindctl` first unless a fresh prerequisite check shows it is available. Prefer the built-in `osascript -l JavaScript` / JXA fallback for creating, editing, and verifying reminders.

## Prerequisites

- **macOS** with Reminders.app
- Install: `brew install steipete/tap/remindctl`
- Grant Reminders permission when prompted
- Check: `remindctl status` / Request: `remindctl authorize`

## When to Use

- User mentions "reminder" or "Reminders app"
- Creating personal to-dos with due dates that sync to iOS
- Managing Apple Reminders lists
- User wants tasks to appear on their iPhone/iPad
- If the user asks for「今天的提醒事項」or the Today view, default to the app-wide Today inventory across all lists; do not narrow to `提醒事項` unless the user explicitly asks for that list.

## When NOT to Use

- Scheduling agent alerts → use the cronjob tool instead
- Calendar events → use Apple Calendar or Google Calendar
- Project task management → use GitHub Issues, Notion, etc.
- If user says "remind me" but means an agent alert → clarify first

## Quick Reference

### View Reminders

```bash
remindctl                    # Today's reminders
remindctl today              # Today
remindctl tomorrow           # Tomorrow
remindctl week               # This week
remindctl overdue            # Past due
remindctl all                # Everything
remindctl 2026-01-04         # Specific date
```

### Sprint-style weekly planning
Use Reminders as a lightweight sprint backlog when the user wants weekly planning:
- Keep three layers: backlog, this week, today.
- Encode priority in the title prefix, e.g. `[P0]`, `[P1]`, `[P2]`.
- Encode size in the title prefix, e.g. `[SP1]`, `[SP3]`, `[SP5]`.
- Use the note/body for next step, dependency, and definition of done.
- Put true deadlines in due dates; items committed for the current week can use Friday 17:59 as the soft weekly cutoff if the user wants a weekly sprint boundary.
- Review unfinished work at the start of the week, then re-plan each workday in a short standup-style check-in.
- When the user asks to decompose one reminder into smaller work items, rewrite the body as 5-8 numbered story-sized subtasks, and explicitly label async/batched/non-blocking processing where relevant.

User-specific planning workflow:
- Keep planning output compact and project-grouped; the user may find broad schedule dumps scattered. Start with the active project/task cluster, then expand to other work only after alignment.
- Reconcile completed/obsolete reminders before scheduling. If the user says they checked items off, re-query only the relevant list/project terms and summarize what remains.
- When a project has multiple reminders that form one workstream, merge them conceptually into a single milestone with explicit gates, owners, due times, and follow-up reminders instead of presenting them as unrelated tasks.
- For delegated work, write the owner into the reminder body (e.g. `負責人：偉甄`) and schedule the work to reduce context switching when the user requests a unified owner.
- For multi-day validation work, separate the setup/run deadline from the observation window; put the observation duration and daily metrics in the body rather than inventing a separate summary task unless the user asks for one.

Priority and effort handling:
- When the user wants reminders marked by importance/effort, keep the title prefix sortable (e.g. `[P0][SP3]`).
- Also set Apple Reminders' built-in `priority` field to mirror the prefix so the item is sortable both visually and by app metadata.
- A practical mapping that worked: `P0 -> priority 9`, `P1 -> priority 5`, `P2 -> priority 1`.
- After bulk writes, verify both the title prefix and the underlying `priority` field on a few items.

See `references/user-reminders-workflow.md` for the user-specific conventions.
- For complex project backlogs, use `references/project-backlog-reminder-template.md` as the reminder body structure (任務背景 / 要執行的內容 / 預期產出 / 驗收標準).
- For urgent ad hoc testing requests, use `references/urgent-test-request-reminders.md`: preserve exact requesters/recipients, env/domain/URL, test conditions, expected fields/values/ranges, deliverable, and acceptance criteria; default same-day urgent items to `[P0]`, priority 9, due 17:59 unless the user gives another deadline.
- For reminders that are driven by a website or other source, gather the source first, then write only the actionable takeaways into the body: source URL, key dates/deadlines, required steps, constraints, and any risk/attention items. Keep it short and operational; do not paste a full page scrape.
- If the user gives a relative deadline like “today 11pm”, translate it into an exact local due time before writing, then verify the stored due date after creation/update.
- For bulk backlog creation from a reviewed plan, prefer `osascript -l JavaScript` with exact-name idempotent create/update, then verify list name, count, and sample titles.
- Topic/list discovery across all reminders lives in `references/reminders-topic-search.md`.
- For agenda requests that combine Calendar events and Reminders due items, use `references/calendar-reminders-agenda-separation.md`: query Calendar.app and Reminders.app separately, then merge in the final summary; use Calendar's `已排程的提醒事項` only as a labeled read-only fallback.
- For today-style requests like「今天有哪些事情要做」or「今天的提醒事項」, scan the whole Reminders app and filter by due date == today; do not limit to the `提醒事項` list unless the user explicitly asks for that list.
- When summarizing today, keep the output short and operational: list calendar first, then reminders, and preserve list name + due time so the user can triage quickly.
- For work schedule planning across projects, use `references/work-schedule-planning.md`: keep plans project-first, convert user decisions into structured reminder bodies, encode owners/deadlines, and verify due dates/counts after writes.
- See `references/apple-reminders-priority-and-bulk-write.md` for the priority mapping and bulk write verification recipe.

## Fallback when `remindctl` isn't available

Use `osascript` for inspection and edits. Prefer `osascript -l JavaScript` when you need structured output or reliable writes; see `references/apple-reminders-applescript.md` for read-only AppleScript notes and `references/apple-reminders-javascript-fallback.md` for known-good JavaScript snippets.

Practical tip: for create/update flows, it is often more reliable to:
1. create the reminder with just a title,
2. set `body` and `due date` in a second pass,
3. verify with `properties of` the reminder.

This avoids edge cases where a single long `make new reminder ... with properties {...}` call does not persist every field as expected.

For write operations, prefer a staged flow: create the reminder with the title first, then set `body` and `due date` separately, and verify by reading back the reminder state. See `references/apple-reminders-session-notes.md` for a known-good recipe.

For write operations, prefer a staged flow: create the reminder with the title first, then set `body` and `due date` separately, and verify with `properties of r`. See `references/apple-reminders-session-notes.md` for a known-good recipe.

- For bulk backlog creation from a reviewed plan, use JXA with a JSON payload and idempotent title matching: find/create the target list, for each item update an existing reminder with the same title or create a new one, set `completed=false`, then verify by reading unfinished count plus the first/last few titles. This avoids duplicate reminder storms when a bulk create script is retried.
- If the target list does not exist yet, create it first with AppleScript/Reminders before running the JXA bulk write. In this session, creating the list first made the bulk write reliable.
- See `references/bulk-backlog-from-plan.md` for the reliable create-then-bulk-write pattern and title-prefix convention.
- For long project plans, prefer detailed reminder bodies over terse one-line items so the reminder remains executable later.
- For undated research-report reminders, you can spread them across future days in groups of three: shuffle the unfinished `研究報告` reminders without due dates, start at tomorrow 00:00 local time, assign due dates in 3-item buckets, and verify that no unfinished `研究報告` reminders remain undated. See `references/research-report-date-batching.md`.
- for exact-title edits/completions, use the `提醒事項` list, match the reminder by exact title when the user gave the full name, then read back `completed` and `dueDate` after writing. See `references/exact-title-update-and-verify.md`.
- when the user asks to read or brief a specific reminder before acting on it, match list + exact title and fetch the full structured properties (`completed`, `dueDate`, `body`, `priority`, `creationDate`, `modificationDate`). See `references/exact-title-read-full-body.md`.
- for bulk moves between lists, snapshot exact titles first, re-resolve each reminder before moving, and verify both source and target counts after the move. See `references/reminders-move-between-lists.md`.
- when the user asks for today's unfinished reminders split by owner (e.g.「分開我跟偉甄的任務」), query all lists due today, inspect reminder bodies for explicit `負責人：...`, and default unowned items to the user. See `references/today-owner-split-inventory.md`.

### Common read-only probes:
- list names: `osascript -e 'tell application "Reminders" to get name of every list'`
- reminders in one list: `osascript -e 'tell application "Reminders" to get name of every reminder of list "Personal"'`
- unfinished inventory for a specific list: `osascript -e 'tell application "Reminders" to get {name, completed} of every reminder of list "提醒事項"'`
- for inventory requests, prefer exact-name lookup of `提醒事項` and filter `completed == false`; keep due dates visible when present
- for inventory requests, prefer exact-name lookup of `提醒事項` and filter `completed == false`; keep due dates visible when present
- if the user says「今天的項目」or asks for the app-wide Today view, query the whole Reminders app for reminders due today across all lists; do not limit to the `提醒事項` list unless the user explicitly says so. See `references/app-wide-today-inventory.md`.
- when querying app-wide Today reminders via JXA, avoid slow full cross-list per-reminder scans and avoid reusing list object references across loop iterations. Use list names, then re-resolve each list with `app.lists.byName(listName)` before running a bounded `whose({completed:false, dueDate:{...}})` query. This avoids timeouts and `Error: 無法取得物件。` observed on 2026-06-05.
- For daily/weekly work planning, first fetch a lightweight unfinished summary only (`list`, `title`, `dueDate`, `priority`) across relevant lists; defer long reminder bodies/notes until the user selects items to schedule. This keeps planning responsive and avoids slow all-list full-body reads.
- For app-wide Today inventory, prefer the compact AppleScript loop in `references/today-inventory-applescript.md`: iterate `repeat with listObj in every list`, re-resolve by list name inside the loop, and guard `due date` reads with `try` so reminders without due dates are skipped cleanly.
- When the user asks to find a reminder/work item across all lists (e.g.「各列表找到…」/ topic search / exact phrase from a task title), do not limit to `提醒事項`: scan every list, search both title and body, and return the list name first so the user can locate the item quickly.
- If the title includes a prefix such as `[P0]`, keep it in the first lookup; if the exact match misses, retry with a normalized title that strips the leading priority tag before giving up.
- When the user asks to mark a just-finished work item complete but does not provide the exact reminder title, search all lists by distinctive title/body terms from the work. If no unfinished reminder matches, do not complete a different same-project or same-day reminder just because it is nearby; report the closest candidates and ask for the exact title/list.
- For cross-list discovery results, keep the output operational and compact: list, title, completed, due date, priority, and a short body snippet or full body only when the user explicitly asks for detail. See `references/cross-list-reminder-detail-report.md` for the exact report shape.
- structured list names / reminder status: use `osascript -l JavaScript` from `references/apple-reminders-javascript-fallback.md`
- read-only probe examples and quoting notes: `references/apple-reminders-read-only-probes.md`
- unfinished-inventory notes and output shape: `references/user-reminders-inventory.md`


### Manage Lists

```bash
remindctl list               # List all lists
remindctl list Work          # Show specific list
remindctl list Projects --create    # Create list
remindctl list Work --delete        # Delete list
```

### Create Reminders

```bash
remindctl add "Buy milk"
remindctl add --title "Call mom" --list Personal --due tomorrow
remindctl add --title "Meeting prep" --due "2026-02-15 09:00"
```

### Complete / Delete

```bash
remindctl complete 1 2 3          # Complete by ID
remindctl delete 4A83 --force     # Delete by ID
```

### Output Formats

```bash
remindctl today --json       # JSON for scripting
remindctl today --plain      # TSV format
remindctl today --quiet      # Counts only
```

## Date Formats

Accepted by `--due` and date filters:
- `today`, `tomorrow`, `yesterday`
- `YYYY-MM-DD`
- `YYYY-MM-DD HH:mm`
- ISO 8601 (`2026-01-04T12:34:56Z`)

## Rules

1. When user says "remind me", clarify: Apple Reminders (syncs to phone) vs agent cronjob alert
2. Always confirm reminder content and due date before creating
3. Use `--json` for programmatic parsing
4. User-specific workflow notes and deadline conventions live in `references/user-reminders-workflow.md` (including the user's evening-time convention: phrases like `晚上` mean the PM version of the stated clock time)
5. If the user asks about Calendar.app events, switching dates, or moving meetings, use the separate `apple-calendar` skill instead of trying to force it into Reminders workflows.

## Related Skills

- `apple-calendar` for Calendar.app event inspection and edits
