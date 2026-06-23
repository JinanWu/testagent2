# Discord mention-gating and free-response notes

This note captures the common case where Discord is connected but appears silent.

## What happened
A connected Discord gateway can still ignore a normal server message if the adapter is configured to require an explicit mention. In that state:
- bot connection looks healthy
- log shows Discord connected
- ordinary channel messages produce no reply
- DMs usually still work

## What to check
1. Confirm the bot is actually online in gateway logs.
2. If messages in a guild channel are ignored, check mention gating first.
3. Verify whether the channel is in `DISCORD_FREE_RESPONSE_CHANNELS`.
4. If the user wants open chat, set `DISCORD_REQUIRE_MENTION=false`.
5. If open access is desired for all users, this is separate from trigger policy and handled by `DISCORD_ALLOW_ALL_USERS=true` or a global allow-all setting.

## Practical rule
- Authorization decides who may talk to the bot.
- Mention gating decides when the bot reacts.

## Useful env/config knobs
- `DISCORD_REQUIRE_MENTION`
- `DISCORD_THREAD_REQUIRE_MENTION`
- `DISCORD_FREE_RESPONSE_CHANNELS`
- `DISCORD_AUTO_THREAD`
- `DISCORD_ALLOWED_CHANNELS`
- `DISCORD_IGNORED_CHANNELS`
- `DISCORD_ALLOW_ALL_USERS`
- `GATEWAY_ALLOW_ALL_USERS`
