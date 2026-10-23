# Docker deployment

MediaMate has a Dockerfile that installs Python dependencies, FFmpeg and Deno, then runs `python -m app.main`. Use it on a host that explicitly supports Docker builds.

Required environment variables: `USER_BOT_TOKEN`, `ADMIN_BOT_TOKEN`, `ADMIN_IDS`, `ADMIN_ACCESS_CODE`. Optional `PORT` defaults to 10000 and `DATABASE_PATH` defaults to `./data/mediamate.sqlite3`.

Mount persistent storage at the directory containing `DATABASE_PATH` (recommended: `/app/data` with `DATABASE_PATH=/app/data/mediamate.sqlite3`) before using the bot for real customers. Without a persistent volume, redeploys/instance replacement can delete database data. Run a single container per local SQLite file. For multiple replicas, use managed PostgreSQL and shared workers instead.

Do not put tokens in Build command. Add them as encrypted environment variables. Revoke any token that was exposed in logs. `/health` and `/ready` are health endpoints; they do not guarantee that a given social media source will permit extraction.
