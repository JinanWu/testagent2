# Calendar event create/verify recipe

Use this when a Calendar event must be created or moved reliably from macOS automation.

Known-good creation pattern:
1. Resolve the target calendar by exact name.
2. Create the event with `app.make({new: 'event', at: calendar, withProperties: {...}})`.
3. Set `summary`, `startDate`, and `endDate` in the initial properties payload.
4. Verify by re-reading the created event via exact summary and calendar name.

Example JXA snippet:
```javascript
const app = Application('Calendar')
const cal = app.calendars.byName('工作')
const ev = app.make({
  new: 'event',
  at: cal,
  withProperties: {
    summary: '跟nession晚餐聊聊',
    startDate: new Date('2026-06-27T16:00:00'),
    endDate: new Date('2026-06-27T20:00:00')
  }
})
console.log(ev.summary())
```

Verification tips:
- Read back `summary`, `start date`, and `end date` with AppleScript after creation.
- If a broad `events()` scan or JXA date filter throws a type-conversion error, fall back to an exact-title query or a smaller bounded query instead of iterating everything.
- For one-off manual scheduling, prefer exact calendar names and a small date window.
