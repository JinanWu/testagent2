---
name: apple-freeform
description: "Read, inventory, and inspect Apple Freeform boards from the local macOS data store."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [Freeform, Apple, macOS, whiteboard, note-taking]
    related_skills: [apple-notes, macos-computer-use]
---

# Apple Freeform

Use this skill when the user asks about Apple Freeform boards / whiteboards / canvases, especially when they want a list of boards, a board title inventory, or a quick inspection of locally cached board metadata.

Freeform is separate from Apple Notes. Do not route Freeform requests through the Apple Notes skill.

## What this skill can do

- Enumerate board titles from the local Freeform snapshot metadata
- Map board UUIDs to titles and activity times
- Inspect the local Freeform database layout when needed
- Recover a usable board inventory even if the GUI is inconvenient

## Preferred approach

1. Read `~/Library/Group Containers/group.com.apple.freeform/Snapshot.plist` first.
   - It usually contains the current board tree and titles.
   - For inventory tasks, prefer this file over spelunking the database.
2. If more detail is needed, inspect `~/Library/Group Containers/group.com.apple.freeform/Boards/boards.db`.
   - Board metadata lives in `boards` and `ckrecord_cache`.
   - Board content blobs may include CRDT/sync data and text fragments, so treat them as structured-but-not-human-friendly data.
3. Use `~/Library/Containers/com.apple.freeform/Data/Library/Caches/BoardPreviewImages/` for visual reading.
   - Decode JSON wrappers with embedded base64 PNG when present.
   - Crop dense boards into smaller regions before vision/OCR; handwritten boards are rarely readable as a single full-frame pass.
4. When the user wants the meaning of a board, convert it into an intermediate representation first.
   - Capture text snippets, images, arrows/relationships, and spatial grouping.
   - Then summarize that representation rather than trying to read the board linearly.

## Quick inventory recipe

- Extract `rootNodes` from `Snapshot.plist`
- Walk the tree and collect each unique `boardIdentifier.storage.boardUUID`
- Keep only non-tombstoned boards
- Sort by `activityTime` descending for the most recent working set

## Data locations

- Snapshot inventory: `~/Library/Group Containers/group.com.apple.freeform/Snapshot.plist`
- Main local DB: `~/Library/Group Containers/group.com.apple.freeform/Boards/boards.db`
- Secondary state: `~/Library/Group Containers/group.com.apple.freeform/Boards/side.db`
- Cached previews: `~/Library/Containers/com.apple.freeform/Data/Library/Caches/BoardPreviewImages/`

## Important caveats

- Board titles may appear multiple times in nested snapshot nodes; deduplicate by UUID before reporting.
- Some records are tombstoned; filter them out unless the user explicitly asks for deleted boards.
- A board may show up in the DB before its preview assets are useful; titles from `Snapshot.plist` are usually the most reliable first pass.
- Preview cache entries may be JSON wrappers containing base64 PNG data, not raw image files.

## Output style

For user-facing inventory, keep it short and directly list the board titles. If useful, include UUID and last activity time in a compact table.

## Support files

- `references/freeform-board-inventory.md` — session-proven inventory notes, schema hints, and extraction tips.
- `references/freeform-board-reading.md` — a concrete recipe for reading one named board via Snapshot.plist, preview cache, vision crops, and board_items.
