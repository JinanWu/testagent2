# Discord gateway session notes

Condensed takeaways from a live setup session.

## What mattered
- The gateway only treated Discord as enabled after `platforms.discord.enabled: true` was present in `~/.hermes/config.yaml`.
- The `discord:` block in `config.yaml` is for behavior/runtime keys (mention handling, thread behavior, backfill, etc.), not for turning the platform on.
- `load_gateway_config()` merges config from `config.yaml` and then computes connected platforms from the enabled platform entries.
- Open access can be expressed with `DISCORD_ALLOW_ALL_USERS=true` or globally with `GATEWAY_ALLOW_ALL_USERS=true`.
- Discord still requires a valid bot token in `~/.hermes/.env` and Message Content Intent in the Discord Developer Portal.

## Quick triage
If Discord is configured but not connecting:
1. Check `hermes gateway status`.
2. Verify `platforms.discord.enabled: true`.
3. Verify the bot token is present in `~/.hermes/.env`.
4. Check the gateway log for login or intent errors.

## When to use
Use this note as a fast reminder when a future session is only about Discord gateway enablement, authorization, or startup triage.
