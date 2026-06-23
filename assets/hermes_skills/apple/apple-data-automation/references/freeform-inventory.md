# Freeform inventory notes

- Read `~/Library/Group Containers/group.com.apple.freeform/Snapshot.plist` first.
- Deduplicate boards by UUID and filter tombstoned records.
- Inspect `boards.db` only if title inventory is not enough.
- Use cached previews plus vision/OCR for dense boards.
- Keep user-facing inventory short: title, UUID, activity time.
