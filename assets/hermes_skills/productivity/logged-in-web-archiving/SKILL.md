---
name: logged-in-web-archiving
description: Capture, export, and organize web pages that require login or session-bound access, without bypassing authentication.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [browser, web, login, export, archive, offline]
---

# Logged-in Web Archiving

Use this skill when the user wants to preserve or work with content that is only visible after login: course lessons, dashboards, knowledge bases, internal tools, forums, or account pages.

## Core rule

Do **not** bypass authentication, session controls, paywalls, or access restrictions.
Only proceed when the user explicitly says they are authorized to access and save the content.
If the page requires login, ask the user to authenticate in the browser session you are controlling, then continue once the protected content is visible.

## When to use

- Exporting a logged-in page for offline reading
- Saving a multi-page course or documentation set
- Capturing an authenticated dashboard or internal tool
- Reconstructing a course into local HTML/PDF/Markdown for later study

## Recommended workflow

1. **Open the target URL in an agent-controlled browser session.**
   - If the page redirects to login, stop and have the user sign in in that same session.
   - Verify access by checking that the protected title/content is visible after login.

2. **Inventory the content before copying anything.**
   - Record the course/page structure: sections, lesson URLs, next/prev links, and any pagination.
   - Prefer a compact outline first; do not start with bulk export until you know the shape.

3. **Capture the page source or rendered DOM.**
   - For a single page, save the HTML plus any immediately required CSS/image assets.
   - For a course, walk each lesson URL and save one HTML file per lesson.
   - Preserve the original URL and timestamp in a small sidecar note.

4. **Keep the export local and organized.**
   - Use a stable folder structure such as `course-title/lesson-01.html`, `lesson-02.html`, etc.
   - If the page relies on relative assets, rewrite them to local paths only if needed for offline viewing.

5. **Verify the result.**
   - Re-open a saved file locally and confirm the lesson text, headings, and images render as expected.
   - If a file is blank or missing assets, inspect whether the content was injected client-side and export the rendered DOM instead of raw HTML.

## Pitfalls

- A login redirect does not mean access is impossible; it usually means you need the user to authenticate in the active browser session.
- Do not assume the user's personal Safari session is visible to the agent. Use the browser session you control.
- For client-rendered sites, raw HTML may be thin; prefer the rendered DOM or a save-after-render approach.
- If the page has many lessons, collect URLs first, then export in a second pass.

## Supporting reference

See `references/offline-web-export.md` for a concise handoff checklist and a practical export recipe.