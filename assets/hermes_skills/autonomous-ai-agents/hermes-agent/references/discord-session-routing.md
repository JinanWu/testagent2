# Discord session routing and switching notes

This note captures the practical way to switch Hermes sessions from Discord and how the home channel fits in.

## Session switching in Discord
- `/new` or `/reset` starts a fresh Hermes session in the current Discord thread/channel.
- `/resume` returns to a previous Hermes session when one exists in session history.
- Different Discord threads can be used as separate workspaces; this is often the easiest way to keep topics isolated.
- The current Discord chat context is not a durable session by itself; the gateway maps Discord messages into Hermes sessions under the hood.

## Home channel behavior
- `/sethome` sets the current Discord chat as the home channel.
- The home channel is where Hermes sends cron job results and cross-platform messages when no more specific target is available.
- If no home channel is set, Hermes may warn and skip some deliveries until one is chosen.

## Practical workflow
- Use one thread for a task, and `/new` when you want a clean slate.
- Use `/resume` when you want to pick up an earlier Hermes session.
- Use `/sethome` only once per preferred delivery channel; it is about message routing, not conversation memory.

## Common confusion
- Closing the browser/chat session does not inherently stop the gateway service.
- Session switching changes the Hermes conversation state, not whether Discord itself is connected.
- If the bot is online but silent in a guild channel, that is usually mention gating or channel authorization, not session state.
