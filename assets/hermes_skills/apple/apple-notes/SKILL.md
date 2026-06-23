---
name: apple-notes
description: "Manage Apple Notes via memo CLI: create, search, edit."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [Notes, Apple, macOS, note-taking]
    related_skills: [obsidian]
prerequisites:
  commands: [memo]
---

# Apple Notes

Use `memo` to manage Apple Notes directly from the terminal. Notes sync across all Apple devices via iCloud.

## Prerequisites

- **macOS** with Notes.app
- Install: `brew tap antoniorodr/memo && brew install antoniorodr/memo/memo`
- Grant Automation access to Notes.app when prompted (System Settings → Privacy → Automation)

## When to Use

- User asks to create, view, or search Apple Notes
- Saving information to Notes.app for cross-device access
- Organizing notes into folders
- Exporting notes to Markdown/HTML

## When NOT to Use

- Obsidian vault management → use the `obsidian` skill
- Apple Freeform / whiteboards → use the `apple-freeform` skill
- Bear Notes → separate app (not supported here)
- Quick agent-only notes → use the `memory` tool instead

## Quick Reference

### Fallback: create a note via AppleScript when `memo` is unavailable

If `memo` is not installed but the user needs the note saved now, use AppleScript directly against Notes.app instead of stopping at setup. A reliable pattern is:

1. Write the note body to a temporary or backup Markdown file first.
2. Create a small `.applescript` file that reads the Markdown as UTF-8, converts newlines to `<br>`, then creates a Notes note.
3. Run `osascript /path/to/script.applescript` and verify by reading the note title back.

Example AppleScript:

```applescript
set mdPath to POSIX file "/Users/<user>/Downloads/note.md"
set noteText to read mdPath as «class utf8»
set AppleScript's text item delimiters to "
"
set parts to text items of noteText
set AppleScript's text item delimiters to "<br>"
set noteBody to parts as text
set AppleScript's text item delimiters to ""

tell application "Notes"
    activate
    set targetFolder to folder "Notes"
    set newNote to make new note at targetFolder with properties {name:"Note Title", body:noteBody}
    return id of newNote
end tell
```

Verify:

```bash
osascript -e 'tell application "Notes" to get name of note id "NOTE_ID_FROM_CREATE"'
```

Keep this as a fallback, not the preferred path: `memo` remains better for search/list/edit workflows.

### View Notes

```bash
memo notes                        # List all notes
memo notes -f "Folder Name"       # Filter by folder
memo notes -s "query"             # Search notes (fuzzy)
```

### Create Notes

```bash
memo notes -a                     # Interactive editor
memo notes -a "Note Title"        # Quick add with title
```

### Edit Notes

```bash
memo notes -e                     # Interactive selection to edit
```

### Delete Notes

```bash
memo notes -d                     # Interactive selection to delete
```

### Move Notes

```bash
memo notes -m                     # Move note to folder (interactive)
```

### Export Notes

```bash
memo notes -ex                    # Export to HTML/Markdown
```

## Limitations

- Cannot edit notes containing images or attachments
- Interactive prompts require terminal access (use pty=true if needed)
- macOS only — requires Apple Notes.app

## Rules

1. Prefer Apple Notes when user wants cross-device sync (iPhone/iPad/Mac)
2. Use the `memory` tool for agent-internal notes that don't need to sync
3. Use the `obsidian` skill for Markdown-native knowledge management
