# Discord gateway setup and runtime notes

This note captures the recurring Discord-specific workflow discovered while configuring Hermes gateway.

## Setup flow
- `hermes gateway setup` is interactive; it walks through platform selection and then Discord-specific prompts.
- For Discord, the setup flow expects:
  - `DISCORD_BOT_TOKEN`
  - `DISCORD_ALLOWED_USERS` or another authorization strategy
  - `DISCORD_HOME_CHANNEL` (home channel ID)
- The token belongs in `~/.hermes/.env`; do not print or commit the raw value.

## Authorization model
- Hermes supports per-platform open access via `DISCORD_ALLOW_ALL_USERS=true`.
- A global fallback also exists: `GATEWAY_ALLOW_ALL_USERS=true`.
- Prefer allowlists over open access unless the user explicitly wants open usage.

## Config mapping
The gateway reads Discord-specific settings from `~/.hermes/config.yaml` under `discord:` and maps them into environment variables. Common keys include:
- `DISCORD_REQUIRE_MENTION`
- `DISCORD_THREAD_REQUIRE_MENTION`
- `DISCORD_FREE_RESPONSE_CHANNELS`
- `DISCORD_AUTO_THREAD`
- `DISCORD_REACTIONS`
- `DISCORD_IGNORED_CHANNELS`
- `DISCORD_ALLOWED_CHANNELS`
- `DISCORD_NO_THREAD_CHANNELS`
- `DISCORD_HISTORY_BACKFILL`
- `DISCORD_HISTORY_BACKFILL_LIMIT`

Important separation:
- `platforms.discord.enabled: true` controls whether Discord is considered enabled by the gateway config loader.
- `discord:` is for behavior/runtime options; it does not by itself enable the platform.
- Open access is separate from enablement: `DISCORD_ALLOW_ALL_USERS=true` (per-platform) or `GATEWAY_ALLOW_ALL_USERS=true` (global) broadens authorization, but the bot still needs to be enabled and configured.

## Verification checklist
1. Check `hermes gateway status` for a loaded service and restart if needed.
2. Confirm the Discord bot token exists in `~/.hermes/.env`.
3. Confirm `platforms.discord.enabled: true` is present in `~/.hermes/config.yaml`.
4. If Discord is silent, verify Message Content Intent is enabled in the Discord Developer Portal.
5. If open access is desired, set `DISCORD_ALLOW_ALL_USERS=true`; otherwise use `DISCORD_ALLOWED_USERS`.

## Discord bot caveat
- Discord bots need Message Content Intent enabled in the Discord Developer Portal or they may fail to receive message text reliably.

## Runtime behavior on laptop sleep/shutdown
- Sleep/standby typically pauses or drops the gateway connection; it may reconnect after wake if the process is still alive.
- Shutdown always stops the process; after boot, the gateway must be started again.
- For always-on usage, prefer a non-sleeping host or a service that survives the machine's normal desktop workflow.
