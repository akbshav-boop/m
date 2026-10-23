# MediaMate — Telegram Media Service

MediaMate is a two-bot Telegram service: a user-facing public media downloader and a separately authenticated admin bot. The same application hosts a health endpoint and a SQLite database.

## What is included

- Russian, Uzbek and English UI. The main navigation uses Telegram's persistent reply keyboard below the message input; choices tied to a particular media link use inline buttons under that media's status message.
- Public media link inspection and downloads through `yt-dlp` for sites supported by its current extractors. This includes many YouTube, Instagram, TikTok, Pinterest, Facebook, X/Twitter, Reddit and direct-media URLs, subject to source-side restrictions.
- Video quality choices, MP3 conversion when available, image/photo handling, and grouping of supported photo/video collections into Telegram albums.
- A cancel-aware download worker that signals and terminates the active `yt-dlp` process, checks cancellation while waiting for a global download slot, and removes temporary files after cleanup.
- Four original transparent monochrome WebP stickers: welcome/play, download, audio, and done. Optional Telegram sticker file IDs can override each sticker individually.
- Profile/preferences, session-only recent history, language settings, support tickets with replies, admin search/blocking, broadcasts, source toggles, limits, reports and database backup.
- Automated Telegram command-menu registration, SQLite migrations and `/health` / `/ready` HTTP endpoints.

**Availability note:** no generic downloader can guarantee every URL, Story, photo or album. Private material, login pages, CAPTCHAs, paid access and other platform restrictions are not bypassed. Story access depends on the public URL and the source's current extractor.

## Database policy

`DATABASE_PATH` defaults to `./data/mediamate.sqlite3`. The database stores user profiles/preferences, support tickets and replies, admin audit actions, service settings, and small daily/source aggregates. It does **not** store individual download URLs, per-download user logs, or saved favorites. The last few links exist in memory for the current running session only and disappear on restart. Aggregate metrics are bounded to the most recent 365 days.

On upgrade from older builds, startup migrates download counts into aggregate counters and drops legacy URL-history/favorites tables. The migration also compacts the legacy SQLite file where possible. Keep a backup before upgrading any database with records you need; ticket records are retained.

SQLite is appropriate for a single running instance. A host must provide persistent storage if tickets and settings must survive redeploys. Do not share a local SQLite file among multiple application replicas; use a server database for that architecture.

## Local setup (Windows, macOS or Linux)

Requires Python 3.11 or later. FFmpeg and Deno are useful for audio conversion, format merging and some YouTube extraction paths. The included Dockerfile installs the system dependencies; generic Python hosting may not execute that Dockerfile.

1. Copy `.env.example` to `.env`.
2. Fill in `USER_BOT_TOKEN`, `ADMIN_BOT_TOKEN`, `ADMIN_IDS`, and a unique `ADMIN_ACCESS_CODE` of at least 12 characters. Set `SUPPORT_USERNAME=suportakbshav` if that is your current support username.
3. Install: `python -m pip install -r requirements-dev.txt`
4. Launch: `python bot.py`
5. Open `http://127.0.0.1:10000/health` and `/ready`.

The public bot should be an administrator of the channel configured by `CHANNEL_ID` if you want instant membership-change notifications. This lets the bot avoid sending subscription invitations to known subscribers and notify a user after a real member-to-nonmember transition. If Telegram does not allow membership status to be verified, the bot suppresses unsolicited subscription ads instead of guessing.

## Environment variables

Required: `USER_BOT_TOKEN`, `ADMIN_BOT_TOKEN`, `ADMIN_IDS` (comma-separated numeric Telegram IDs), `ADMIN_ACCESS_CODE` (minimum 12 characters). See `.env.example` for database path, support username, channel, time limits, download concurrency, report schedule and optional sticker IDs. Keep `.env` and actual bot tokens private. If a token has been exposed in logs or chat, revoke it in BotFather and replace it.

## User commands

`/start`, `/menu`, `/download`, `/cancel`, `/help`, `/profile`, `/stats`, `/history`, `/clearhistory`, `/settings`, `/language`, `/support`, `/privacy`, `/id`, `/about`.

Download flow: send a public HTTPS link; the bot inspects the available media, then presents supported actions in inline buttons. The persistent keyboard is reserved for primary navigation. `/history` is session-only and displays source hosts rather than full query-string URLs.

## Admin commands

Use `/start` and enter the admin access code. The session expires after inactivity. Available commands: `/panel`, `/stats`, `/backup`, `/user ID_or_username`, `/block ID`, `/unblock ID`, `/tickets`, `/reply TICKET_ID text`, `/close TICKET_ID`, `/report`, `/ytdlp_status`, `/update_ytdlp`, `/recentdownloads` (aggregate counts only), `/dbinfo`, `/audit`, `/setlimit file_mb|duration_min|cooldown VALUE`, `/source SOURCE on|off`, `/broadcast_ru`, `/broadcast_uz`, `/broadcast_en`, `/cancel`, `/logout`.

## Performance and limitations

The downloader prefers a progressive MP4 when available to avoid a separate merge step, uses concurrent fragments for segmented media, and reports speed/ETA when yt-dlp provides them. Actual speed depends on the source, media format, server network and Telegram upload. YouTube may reject automated server requests; an updated yt-dlp or different host does not guarantee access.

## Tests

Run `python -m compileall -q app tests bot.py` and `python -m pytest -q`. The unit suite covers URL-history migration/purging, ticket lifecycle, aggregate metrics, locale coverage, media classification and downloader command arguments. Unit tests do not establish that live tokens, every source, channel membership updates or the target host work end-to-end.

## Deployment guides

- [`DEPLOYMENT.md`](DEPLOYMENT.md)
- [`RENDER_DEPLOY.md`](RENDER_DEPLOY.md)
- [`FADEHOST_DEPLOY.md`](FADEHOST_DEPLOY.md)
- [`NORTHFLANK_DEPLOY.md`](NORTHFLANK_DEPLOY.md)


## Speed-first download defaults

The default video mode prefers a progressive MP4 at up to 720p when the source provides one, uses up to 8 concurrent fragments for segmented media, and does not run a package-index update at startup. These choices can reduce overhead and download size, but cannot override source-side throttling, poor network routes, or Telegram upload limits. Higher-quality options remain available. Set `YTDLP_UPDATE_ON_START=true` only if you explicitly want startup update checks.
