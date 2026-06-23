# Dashboard Summary Scroll Reset Pattern

Use this when a summary/details panel seems not to refresh after switching to a shorter item, but moving the mouse or interacting with the panel makes it appear updated.

## Symptom
- Previous item has a longer summary.
- Next item has a shorter summary.
- The text is updated in state, but the visible panel still appears stuck until hover/mouse movement or another interaction.

## Likely cause
A scrollable container preserves its previous scroll position (`scrollTop`) when the rendered text changes.
This is common when the content area uses:
- `overflow-y-auto`
- `flex-1` inside a fixed-height panel
- conditional rendering without a changing `key`

## What to check
1. Verify the state value really changed (log or inspect the rendered prop).
2. Confirm the visible text node is re-rendered, not just the parent panel.
3. Check whether the summary area is its own scroll container.
4. Check whether the component is reused across items without a remount.

## Typical fixes
- Reset the scroll container on content change: `ref.current.scrollTop = 0`
- Add a changing `key` to force remount when the selected item changes
- If the panel is meant to show the full text, avoid reusing a preview-sized scroll state from the previous item

## Verification
- Switch from a long summary to a short summary.
- The visible text should immediately show the new content without hover/mouse movement.
- The scroll position should start at the top for each new selection.