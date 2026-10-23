# MediaMate command and button reference

## User bot

Primary navigation is shown in the persistent Telegram reply keyboard below the input field. Contextual actions (video quality, MP3, cancel) appear as inline buttons attached to the corresponding status message. Select Russian, Uzbek or English when prompted on first launch.

- `/start` — start or reopen the bot
- `/menu` — show the welcome and primary keyboard
- `/download` — prompt for a public media URL
- `/cancel` — cancel the current step, metadata lookup or active `yt-dlp` download
- `/help` — instructions
- `/profile` — profile and preferences
- `/stats` — current-process session counts
- `/history` — recent hosts/statuses from the current process memory only
- `/clearhistory` — clear session history
- `/settings` — language, default quality and output type
- `/language` — change interface language
- `/support` — create a persistent ticket, inspect/close own tickets, or contact `@suportakbshav`
- `/privacy` — explain exactly what is kept in the database
- `/id` — show the numeric Telegram ID
- `/about` — service information

Send a public HTTPS link without any command. Private material, authentication walls or other access protections are not bypassed.

## Admin bot

Use `/start` and enter `ADMIN_ACCESS_CODE`. Authorized admin IDs are configured through `ADMIN_IDS`. Commands:

- `/panel` — open the dashboard
- `/stats` — service counters
- `/backup` — create and send a consistent database backup
- `/dbinfo` — database file size and row counts, without showing ticket contents
- `/user ID_or_username` — search for a user
- `/block ID`, `/unblock ID` — access controls and stop an active download on block
- `/tickets` — list open support tickets
- `/reply TICKET_ID text`, `/close TICKET_ID` — reply to or close a ticket
- `/report` — ten-day metrics report
- `/recentdownloads` — compact daily/source aggregates only; it does not display individual URLs
- `/audit` — recent admin actions
- `/ytdlp_status`, `/update_ytdlp` — downloader diagnostics and update
- `/setlimit file_mb|duration_min|cooldown VALUE` — change limits
- `/source SOURCE on|off` — toggle a supported source
- `/broadcast_ru`, `/broadcast_uz`, `/broadcast_en` — preview and confirm language-specific broadcasts
- `/cancel` — cancel the current admin workflow
- `/logout` — close the admin session

Telegram commands are registered on startup. A registered command is not proof of successful interaction with Telegram; live bot tokens and permissions must be checked on the target host.

## HTTP endpoints

- `/` — basic service status
- `/health` — health check
- `/ready` — readiness (storage initialized)

These are HTTP endpoints, not Telegram commands.
