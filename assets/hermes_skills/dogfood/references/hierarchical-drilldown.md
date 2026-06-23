# Hierarchical drill-down QA note

Use this when testing dashboards or explorer-style UIs where each click should reveal a deeper level.

Session takeaway:
- The UI may implement a full drill path (e.g. region -> line -> group -> product -> tour) while the backend data only contains children up to an intermediate level.
- If the UI stops after ~N clicks, verify whether the currently selected node actually has children in the API response before labeling it a click/interaction bug.
- In this session, the dashboard endpoint returned regions, lines, groups, and products, but no tours under any product. The visible behavior was therefore a data-depth limitation rather than a broken click handler.

Recommended checks:
1. Confirm the click target still changes stack/breadcrumb state in the frontend.
2. Inspect the hierarchy payload and count children at each level.
3. Record whether the deepest available node is an actual terminal node in the data.
4. In the report, distinguish:
   - Functional UI bug: click does nothing even though data exists.
   - Content/data limitation: click works, but the API has no deeper children to show.
