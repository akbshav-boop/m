# MediaMate — FadeHost deployment (Python runtime)

The repository root contains `bot.py` so generic Python hosts that default to `python bot.py` can launch it. Both Telegram bots, the health server and scheduled maintenance tasks run from the same application process.

## FadeHost settings

- Runtime: **Python**
- Build command: `pip install -r requirements.txt`
- Start command: `python bot.py` (only if the dashboard exposes a separate start-command field; if it already runs this by default, keep it)
- Never put a Telegram token in Build command. Tokens belong in encrypted Environment variables.
- `PORT=10000` is an acceptable default if a port value is required; the health server binds to `0.0.0.0`.

## Required environment variables

- `USER_BOT_TOKEN` — public/user bot token
- `ADMIN_BOT_TOKEN` — separate admin bot token
- `ADMIN_IDS` — comma-separated numeric Telegram user IDs
- `ADMIN_ACCESS_CODE` — generated secret of at least 12 characters
- `DATABASE_PATH=./data/mediamate.sqlite3` — SQLite file path

Use `.env.example` for optional values. Do not commit `.env`, token values, or the SQLite database to GitHub. If a token has appeared in build logs or chat, revoke it via BotFather and replace it before deployment.

## Database persistence

The app uses SQLite and automatically initializes its schema. SQLite survives ordinary process restarts when the file remains on disk. It does **not** overcome ephemeral filesystems: if FadeHost removes local files when redeploying, replacing an instance, or sleeping, saved profiles, settings, support tickets, audit history and reports can be lost. Recent link history is intentionally session-only and download URLs/favorites are not stored in the database. For a client-facing paid service, attach a persistent disk/volume or migrate to a managed database and schedule backups. Use one app instance for a local SQLite file; multiple replicas need a server database and shared queue.

## System dependencies

The included Dockerfile installs FFmpeg and Deno. A generic Python runtime may ignore it and may not provide those binaries. The app can start without them, but MP3 conversion, format merging or some YouTube extraction paths may fail. Test the actual source list on the target host; do not promise universal downloads.

## Verification after deployment

1. Confirm the logs show application startup and no `can't open file '/data/app/bot.py'` error.
2. Open `/health` and `/ready` if the host exposes the service URL.
3. Test `/start`, locale switching, one public URL, cancel, ticket creation/reply and admin button controls with private test accounts.
4. Restart the service and verify that the same SQLite file and settings are still present.

Passing unit tests is not the same as verifying a live Telegram deployment or successful extraction from every media platform.
