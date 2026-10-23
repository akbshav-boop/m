# MediaMate on Render

## Recommended service type

Create a **Web Service** from the GitHub repository and select the **Docker** runtime so Render builds the repository's `Dockerfile`. This Dockerfile installs the Python packages plus FFmpeg and Deno, which are needed for some media conversions and YouTube extractor paths. A generic Python runtime may not include these system packages.

- Dockerfile path: `./Dockerfile`
- Build command: leave unset for a Docker service; Render builds the Dockerfile.
- Start command: use the Dockerfile default (`python -m app.main`).
- Health check path: `/health` (health check only confirms app readiness, not that every website download works).
- Required environment variables: `USER_BOT_TOKEN`, `ADMIN_BOT_TOKEN`, `ADMIN_IDS`, `ADMIN_ACCESS_CODE`.
- Optional: `PORT` (Render provides a port; the app defaults to `10000`), `DATABASE_PATH`.

Never put a Telegram token in Build command or commit tokens to GitHub. Revoke any token exposed in old logs and set the replacement only in Render's environment-variable settings.

## Important free-tier limits

Render documents that Free web services spin down after 15 minutes without inbound HTTP requests/WebSocket messages, and Free web services cannot attach persistent disks. Telegram bot polling is outbound traffic, so do not assume it will keep the service awake. When the web service sleeps, the bots stop processing updates until the service spins back up. Free instances can also restart, and their local filesystem changes are ephemeral. Use Free only for evaluation/private testing, not as a reliable paid customer service.

## Database path

The app defaults to `DATABASE_PATH=./data/mediamate.sqlite3`. On Render this local file is ephemeral. For reliable persistent data, use an appropriate paid service with persistent storage and set `DATABASE_PATH` to a file under the configured disk's mount path, then test read/write permissions after deployment. A managed database is another option but requires a database adapter; this codebase currently ships with SQLite, not a PostgreSQL adapter. Back up the SQLite file using the admin `/backup` command and store backups securely.

## Before sharing the bot with customers

1. Test the User Bot and Admin Bot in private chats.
2. Confirm `/health` and `/ready` respond and logs show the application started.
3. Test a public YouTube link, an audio conversion, an accessible link from each other source you advertise, cancellation, session-only history behavior, and support tickets. Some platform extractors can be blocked or change at any time.
4. Restart/redeploy and verify that data is retained at the configured storage path.
5. Publish clear usage rules, privacy/retention information and a support contact. Do not promise that every link works or every download finishes within a fixed time.
