# QA report — MediaMate revision

## Workspace checks

- `python -m compileall -q app tests bot.py` succeeds.
- `python -m pytest -q`: **15 passed** in the current workspace.
- Tests cover support ticket lifecycle, aggregate statistics, download URL/favorites table removal, migration of an older database containing URL records, locale keys and downloader argument construction.
- Four local 512×512 transparent WebP monochrome stickers are bundled: `hello.webp`, `download.webp`, `music.webp`, `done.webp`.
- URL history and favorites are no longer persisted. Existing legacy per-link tables are dropped during schema migration; aggregate counters are retained where available. Tickets and replies remain stored.
- Download cancellation uses a cancellation event, terminates the active `yt-dlp` process and checks the event while waiting for a global download slot.
- Main navigation now uses a reply keyboard. The per-link format choices and cancel action remain inline controls tied to the relevant status message.

## Not verified end-to-end

- No live Telegram API polling was performed with the owner's tokens. Command registration, reply keyboard appearance, album delivery and bot permissions still require an actual run.
- The user bot must be an administrator of the channel to receive chat-member updates; subscription-check behavior is dependent on the channel ID and Telegram's returned member status.
- Download performance and support for each public URL depend on extractor support, network, hosting region, file size, FFmpeg/Deno availability, Telegram limits and source-side restrictions.
- Persistent ticket storage across redeploys depends on the host retaining `DATABASE_PATH`. A persistent disk or managed server database is required if the runtime filesystem is ephemeral.

The tests are not a claim that all live bot commands or all media sources have been verified. Rotate any previously exposed bot token before deployment.
- Runtime import / live startup could not be smoke-tested in the authoring container because `aiogram` is not installed there and package download was unavailable. The project was syntax-compiled and unit-tested; run the startup check in the owner's existing `.venv`, where the dependencies were installed.
