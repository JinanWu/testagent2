# Large TSX Patching Notes

## Session pattern that mattered
A large dashboard component was edited in stages. A partial read plus a patch intended to add a notice card caused malformed JSX insertion risk because the insertion point was not re-checked against the exact return tree.

## Safe recovery pattern
1. Revert the file to the known-good state if a patch lands in the wrong place.
2. Re-read the exact region around the intended anchor.
3. Apply a narrow replace against a unique nearby line.
4. Inspect `git diff` around the insertion point.
5. Run `git diff --check` before claiming success.

## Useful rule of thumb
If the file is a large React component, never patch from memory of a paginated excerpt. Re-open the component around the exact line where the new JSX will live.
