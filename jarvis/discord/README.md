# Jarvis in Discord

A Discord bot on the homebase (the 5060 laptop) that lets Jake talk to Jarvis and see Jarvis alerts in his server.

| Where | What happens |
|---|---|
| **#jarvis** (no @ needed), an @mention anywhere, or a DM | The message goes to TARS (`POST http://127.0.0.1:8790/chat`) and the reply comes back. Only the application's owner (Jake) and `DISCORD_ALLOWED_USERS` can talk to it, since TARS can file cards. |
| **#jarvis-alerts** | Watchdog alerts (the same ones pushed to the phone), anything POSTed to `http://127.0.0.1:8796/notify` (`{"title","body","url"}`), and each card that becomes **staged** or **needs-jake** in `flanneryjake/jarvis-tasks` (checked every 2 min). Staged cards without `pin` get an **Approve** button (Jake only; same as approving in the phone app). PIN cards link to GitHub and still need the phone app. |

The bot makes #jarvis and #jarvis-alerts itself when it joins a server. It leaves any server Jake doesn't own
(unless its id is in `DISCORD_ALLOWED_GUILDS`), since the bot is public and anyone with the link could add it.

## Setup

1. Invite link (one click; permissions: View Channels, Send Messages, Embed Links, Read Message History, Manage Channels):
   `https://discord.com/oauth2/authorize?client_id=1555659760454475856&scope=bot%20applications.commands&permissions=85008`
2. On the homebase, `install-discord.ps1` (header of the file) saves the bot, installs `discord.py` for the user,
   takes the token from `C:\Jarvis\secrets\discord-bot.txt` (or from the rig over `tailscale file`), and registers
   the **Jarvis Discord** task (at logon, re-started every 5 min if it died). No admin needed.

The Message Content intent must be on (Developer Portal, Bot, Privileged Gateway Intents); for a bot in fewer than
100 servers it can also be turned on with `PATCH /applications/@me {"flags": flags | 524288}`.

Token: `C:\Jarvis\secrets\discord-bot.txt` (only Jake's user and SYSTEM can read it) or env `DISCORD_BOT_TOKEN`.
Never in git, chat, project files or logs. Log: `C:\Jarvis\discord\discord.log`. Health: `GET http://127.0.0.1:8796/health`.

Settings (env, optional): `TARS_URL`, `DISCORD_ALLOWED_USERS` (comma-separated user ids), `DISCORD_CHAT_CHANNEL`,
`DISCORD_ALERT_CHANNEL`, `DISCORD_CARD_POLL` (seconds), `DISCORD_NOTIFY_PORT`.

Tests: `python jarvis/discord/test_jarvis_discord.py` (needs `discord.py` installed).
