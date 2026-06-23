# Moving reminders between lists

Use this when a user wants to relocate a set of reminders from one list to another, especially for topic-based buckets like research reports.

## Reliable pattern

1. Ensure the target list exists.
2. Read the source list and snapshot the exact titles to move.
3. Re-fetch each reminder by exact title from the source list before moving it.
4. Move each reminder to the target list using JXA/Reminders objects.
5. Verify the source list no longer contains those titles and the target list now does.

## Why this pattern

Direct AppleScript loops over a live reminder collection can fail with `Error: 無法取得物件。 (-1728)` during bulk moves. Snapshotting the titles first and then re-resolving each reminder avoids that issue.

## Example JXA sketch

```javascript
const R = Application('Reminders');
const source = R.lists.byName('提醒事項');
const target = R.lists.byName('投資研究');
const names = source.reminders()
  .map(r => r.name())
  .filter(name => name.startsWith('閱讀投顧研究報告：'));

for (const name of names) {
  const item = source.reminders().filter(r => r.name() === name)[0];
  if (!item) continue;
  try { item.move({to: target}); }
  catch (e1) {
    try { item.move(target); }
    catch (e2) { item.list = target; }
  }
}
```

## Verification

- `sourceCount` for the moved prefix should be `0`
- `targetCount` for the moved prefix should match the moved set
- Re-read both lists after the move; do not assume success from the move call alone
