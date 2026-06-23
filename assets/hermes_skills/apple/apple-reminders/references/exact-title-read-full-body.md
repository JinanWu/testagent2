# Exact-title full-body read for Apple Reminders

Use this when the user asks to inspect or brief a specific reminder before working on it.

Pattern:
- Match both list name and exact reminder title when the user provides them.
- Read back structured fields, not only the title: `completed`, `dueDate`, `body`, `priority`, `creationDate`, `modificationDate`.
- Preserve the body sections when reporting: 任務背景 / 要執行的內容 / 預期產出 / 驗收標準.
- Interpret Apple Reminders priority alongside any title prefix: `9` = high / `[P0]`, `5` = medium / `[P1]`, `1` = low / `[P2]`, `0` = none.

Known-good JXA probe:

```bash
osascript -l JavaScript <<'JXA'
const app = Application('Reminders');
app.includeStandardAdditions = true;
const targetList = '提醒事項';
const targetTitle = 'EXACT TITLE HERE';
let found = [];
for (const lst of app.lists()) {
  if (lst.name() !== targetList) continue;
  for (const r of lst.reminders()) {
    const p = r.properties();
    if (p.name === targetTitle) {
      found.push({
        name: p.name,
        completed: p.completed,
        dueDate: p.dueDate ? new Date(p.dueDate).toLocaleString('zh-Hant-TW', {hour12:false}) : null,
        body: p.body || '',
        priority: p.priority,
        creationDate: p.creationDate ? new Date(p.creationDate).toLocaleString('zh-Hant-TW', {hour12:false}) : null,
        modificationDate: p.modificationDate ? new Date(p.modificationDate).toLocaleString('zh-Hant-TW', {hour12:false}) : null
      });
    }
  }
}
console.log(JSON.stringify(found, null, 2));
JXA
```

Reporting shape:
- Start with list, title, status, due date, priority.
- Then summarize the body in operational terms: background, concrete steps, deliverables, acceptance criteria.
- If the body indicates a likely technical interpretation, label it as interpretation rather than stored reminder text.
