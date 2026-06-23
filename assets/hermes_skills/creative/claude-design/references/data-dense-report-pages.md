# Data-dense report pages in CLI/API mode

Use this pattern when the user gives you a bundle of personal metrics or weekly logs and asks for both a written report and a polished HTML visualization.

Core rules:
- Treat the Markdown report as the source of truth for prose; do not invent missing timestamps, measurements, or categories.
- Surface missing data explicitly in the final artifact (e.g. exact sleep/wake times, omitted weights, omitted steps, missing chest circumference).
- Build a single self-contained HTML file when the user wants a shareable page; embed CSS/JS and prefer inline SVG for lightweight charts.
- Include both narrative summary and quick-scan metric cards so the page works as a report and a dashboard.
- Use tables for daily logs; use compact visualizations for trend lines and distribution cues.
- Keep the tone calm and premium for health/journal content; avoid generic dashboard clutter.
- If the page is generated from a prompt to another coding agent, give explicit requirements about the missing-data policy and file name.

Verification checklist:
- Confirm the HTML file exists at the expected absolute path.
- Confirm the file contains the required missing-data notes and the major sections requested by the user.
- If browser preview is unavailable, verify by reading the file and searching for the key markers rather than guessing.
- Keep the final answer short: path, what it contains, and verification status.
