# Freeform board inventory notes

This file captures the practical extraction pattern for Apple Freeform boards on macOS.

## What worked

- The most reliable first-pass source for board titles was:
  - `~/Library/Group Containers/group.com.apple.freeform/Snapshot.plist`
- That plist contains a `rootNodes` tree with board entries.
- Each board entry includes:
  - `boardIdentifier.storage.boardUUID`
  - `title`
  - `activityTime`
  - `creationTime`
  - tombstone flags

## Inventory workflow

1. Load `Snapshot.plist` with `plistlib`.
2. Walk `rootNodes` recursively.
3. Deduplicate by board UUID.
4. Keep only entries where `isTombstoned == false`.
5. Sort by `activityTime` descending.
6. Report titles first; add UUID and timestamps only if useful.

## Relevant local paths

- `~/Library/Group Containers/group.com.apple.freeform/Snapshot.plist`
- `~/Library/Group Containers/group.com.apple.freeform/Boards/boards.db`
- `~/Library/Group Containers/group.com.apple.freeform/Boards/side.db`
- `~/Library/Containers/com.apple.freeform/Data/Library/Caches/BoardPreviewImages/`

## Database notes

- `boards.db` contains the main board metadata tables.
- `ckrecord_cache` records are NSKeyedArchiver blobs; `RecordType` commonly showed `BoardMetadata`.
- `boards` rows include board UUIDs and activity timing, but titles were easier to get from `Snapshot.plist`.

## Gotchas

- `Snapshot.plist` may include duplicate board entries in nested nodes. Deduplicate by UUID.
- Preview cache files may be JSON wrappers with embedded base64 PNG data, not raw image files.
- Avoid assuming every visible board is active; filter tombstoned entries.

## Example outcome shape

- `未命名`
- `團帳`
- `跟瑋玲討論的內容`
- `AI平台`

