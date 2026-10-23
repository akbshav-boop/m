"""Safe runtime coordination for yt-dlp operations and self-updates.

Updates are serialized against yt-dlp subprocesses so package files are not
replaced while a download is running. This module does not attempt to bypass
site authentication, CAPTCHAs, anti-bot checks, or access restrictions.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

log = logging.getLogger(__name__)
_condition = asyncio.Condition()
_active_operations = 0
_update_in_progress = False
_update_lock = asyncio.Lock()
_last_update_attempt = 0.0


def current_version() -> str:
    try:
        return importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


@asynccontextmanager
async def ytdlp_operation():
    """Register an active yt-dlp process; updates wait until all finish."""
    global _active_operations
    async with _condition:
        await _condition.wait_for(lambda: not _update_in_progress)
        _active_operations += 1
    try:
        yield
    finally:
        async with _condition:
            _active_operations = max(0, _active_operations - 1)
            _condition.notify_all()


async def update_ytdlp(*, force: bool = False, timeout: int = 180) -> dict[str, str | bool]:
    """Upgrade yt-dlp[default] in the current Python environment.

    A writable virtual environment is recommended. Failure is non-fatal to the
    service; the current installed version remains available if pip fails.
    """
    global _update_in_progress, _last_update_attempt
    async with _update_lock:
        now = time.monotonic()
        cooldown = int(os.getenv("YTDLP_UPDATE_COOLDOWN_SECONDS", "900"))
        if not force and now - _last_update_attempt < cooldown:
            return {"ok": False, "version": current_version(), "message": "Update cooldown is active."}
        before = current_version()
        _last_update_attempt = now
        async with _condition:
            _update_in_progress = True
            try:
                await asyncio.wait_for(_condition.wait_for(lambda: _active_operations == 0), timeout=timeout)
            except asyncio.TimeoutError:
                _update_in_progress = False
                _condition.notify_all()
                return {"ok": False, "version": before, "message": "Active media operations did not finish before the update timeout."}
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", "--no-input", "yt-dlp[default]",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return {"ok": False, "version": current_version(), "message": "pip upgrade timed out."}
            after = current_version()
            if proc.returncode == 0:
                log.info("yt-dlp update finished: %s -> %s", before, after)
                return {"ok": True, "version": after, "message": "Updated successfully." if after != before else "Already up to date."}
            tail = output.decode("utf-8", "replace")[-700:].strip()
            log.warning("yt-dlp update failed (exit %s): %s", proc.returncode, tail[-250:])
            return {"ok": False, "version": after, "message": "Package update failed. Check server logs and ensure the app environment is writable."}
        except asyncio.CancelledError:
            if proc is not None and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except (ProcessLookupError, asyncio.TimeoutError):
                    if proc.returncode is None:
                        proc.kill()
                    await proc.wait()
            raise
        except (OSError, asyncio.SubprocessError) as exc:
            log.exception("Could not run yt-dlp updater")
            return {"ok": False, "version": current_version(), "message": f"Updater could not start ({type(exc).__name__})."}
        finally:
            async with _condition:
                _update_in_progress = False
                _condition.notify_all()


async def daily_update_loop(is_busy, notify_admins, *, interval_seconds: int = 86400) -> None:
    """Update at startup and daily; retry later if jobs are active or the index is down."""
    startup = os.getenv("YTDLP_UPDATE_ON_START", "false").strip().lower() in {"1", "true", "yes", "on"}
    next_attempt = time.monotonic() if startup else time.monotonic() + interval_seconds
    while True:
        wait_seconds = max(1.0, next_attempt - time.monotonic())
        await asyncio.sleep(min(wait_seconds, 60.0))
        if time.monotonic() < next_attempt:
            continue
        try:
            if is_busy():
                log.info("Delaying scheduled yt-dlp update because media jobs are active")
                next_attempt = time.monotonic() + 60
                continue
            result = await update_ytdlp(force=True)
            if not result["ok"]:
                log.warning("Scheduled yt-dlp update not completed: %s", result["message"])
                notice = notify_admins(f"⚠️ Автообновление yt-dlp не выполнено. Версия: {result['version']}. Причина: {result['message']}")
                if asyncio.iscoroutine(notice):
                    await notice
                # Retry an unsuccessful package update in one hour, without restarting the bot.
                next_attempt = time.monotonic() + 3600
            else:
                next_attempt = time.monotonic() + interval_seconds
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduled yt-dlp update failed; bot will continue")
            next_attempt = time.monotonic() + 3600
