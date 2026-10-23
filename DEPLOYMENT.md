# MediaMate deployment checklist

- Python 3.11+; install with `python -m pip install -r requirements.txt` (or `requirements-dev.txt` for tests).
- Start command: `python bot.py`.
- Required environment variables: `USER_BOT_TOKEN`, `ADMIN_BOT_TOKEN`, `ADMIN_IDS`, `ADMIN_ACCESS_CODE`.
- Recommended settings: `DATABASE_PATH=./data/mediamate.sqlite3`, `SUPPORT_USERNAME=suportakbshav`, `CHANNEL_ID=@akbshav_channel`, `CHANNEL_URL=https://t.me/akbshav_channel`.
- Make the user bot an administrator of the configured channel to receive member-removal updates. If membership status cannot be verified, the bot suppresses the invitation rather than advertising to a possibly subscribed user.
- Never store tokens in source code, Build commands, screenshots or Git. If a token was exposed, revoke and replace it.
- SQLite stores persistent profiles/preferences, tickets/replies, admin audit, settings and aggregate daily/source counters. It does not store individual download URLs, link history or favorites. Session history resets on restart. Keep the SQLite file on persistent storage to preserve tickets and settings.
- Use one service instance for a local SQLite file. Multiple replicas need a shared database and job queue.
- The included Dockerfile installs FFmpeg and Deno. Generic Python runtimes may ignore Dockerfile; verify MP3, format merging and YouTube extraction on the actual host.
- `/health` and `/ready` indicate service/storage status, not universal source compatibility.
- Before public launch, test both bots, language switching, a public link, cancellation, support ticket creation/reply, channel membership notifications, database backup and process restart.
