# Cursor compatibility notes

Session takeaway:

- Hermes does not ship a native `cursor`/Cursor IDE model provider.
- Hermes does support Cursor project rules as context: `.cursorrules` and `.cursor/rules/*.mdc` are loaded when no higher-priority project context file is present.
- If a user has a Cursor-compatible or proxy API, Hermes can potentially use it via a custom model provider / `base_url` configuration, but that is a custom integration rather than a built-in provider.

Verification hook:

- Check `hermes model --help` and the model-provider registry under `plugins/model-providers/` when evaluating what providers are actually available in the current build.
