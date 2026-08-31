# Freeform board reading notes

Session-proven workflow for reading a specific Freeform board rather than just inventorying titles.

## Target board

- Title: `跟瑋玲討論的內容`
- UUID: `C976FC1A60D94E8893FA13B0BBC8C2E4`

## What worked

1. Find the board in `Snapshot.plist` to get the canonical title/UUID mapping.
2. Look up preview cache assets in:
   - `~/Library/Containers/com.apple.freeform/Data/Library/Caches/BoardPreviewImages/`
3. Decode the preview wrapper if it is JSON with embedded `codableImage.pngImageData`.
4. Run vision on the full preview first, then crop into quadrants or focused regions for handwritten text.
5. When the board is still too dense, inspect `boards.db`:
   - `board_items` row counts by board UUID and item type give a quick sense of structure.
   - `specific_data` blobs often begin with `crdt` and contain parseable text fragments.

## Practical reading pattern

- Do not try to read Freeform as a single linear document.
- Convert it into an intermediate representation with:
  - text boxes / handwritten snippets
  - images / attachments
  - shapes / arrows / connectors
  - spatial relations (left/right/top/bottom, grouping, proximity)
- Then summarize that intermediate representation for the user.

## Useful signals

- `item_type=10` was present in at least 24 rows for this board.
- `Snapshot.plist` is still the most reliable place for titles.
- Preview images are often more useful than raw DB blobs for first-pass semantic reading.

## Caveats

- Handwriting is often only partially legible from the preview.
- Blob string extraction can yield fragments, not clean structure.
- Vision works best on cropped regions, not the whole board at once.
