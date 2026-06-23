# Offline web export handoff checklist

Use this when the user has legitimate access to a logged-in web page and wants an offline copy.

## Quick handoff

- Confirm the user is authorized to save the content.
- Open the target URL in the agent-controlled browser session.
- If the page redirects to login, let the user sign in there.
- Verify the protected content is visible after authentication.
- Export page-by-page rather than trying to mirror everything blindly.

## Practical export order

1. Collect the lesson or section URL list.
2. Save each lesson as HTML once rendered.
3. Save images and linked assets if the page depends on them.
4. Keep a small `index.html` or `index.md` with the course outline and original URLs.
5. Spot-check the saved files offline.

## Good metadata to keep

- Original URL
- Saved filename
- Save timestamp
- Section / lesson title
- Any missing assets or interactive elements

## When the HTML is thin

Some sites render content client-side. In that case:

- Save the rendered DOM instead of raw view-source HTML.
- If necessary, capture the visible text into Markdown alongside the HTML.
- Verify headings, body text, and images after saving.

## What not to do

- Do not try to bypass login, session checks, or paywalls.
- Do not use the user's personal browser session unless they explicitly want that workflow and it is technically accessible to the agent.
- Do not bulk-export without first checking that the content tree is what you expect.
