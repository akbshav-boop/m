from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit
from typing import Awaitable, Callable

from app.config import settings
from app.ytdlp_runtime import ytdlp_operation

ProgressCallback = Callable[[dict[str, str]], Awaitable[None]]
runtime_file_size_mb = settings.max_file_size_mb
def set_runtime_file_size_mb(value: int):
    global runtime_file_size_mb
    max_allowed = 2000 if settings.telegram_api_base_url else 45
    runtime_file_size_mb = max(1, min(max_allowed, int(value)))
ProcessCallback = Callable[[asyncio.subprocess.Process | None], None]


class DownloadError(RuntimeError):
    pass


class DownloadCancelled(DownloadError):
    pass


def _clean_text(value: object, limit: int = 250) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def media_summary(info: dict) -> dict[str, object]:
    entries = [e for e in (info.get("entries") or []) if isinstance(e, dict)]
    formats = list(info.get("formats") or [])
    for entry in entries:
        formats.extend(entry.get("formats") or [])
    heights = sorted({int(f.get("height")) for f in formats if f.get("height") and f.get("vcodec") not in (None, "none")}, reverse=True)
    image_exts = {"jpg", "jpeg", "png", "webp", "gif", "avif"}
    ext = str(info.get("ext") or "").lower()
    has_video = bool(info.get("vcodec") not in (None, "none") or any(f.get("vcodec") not in (None, "none") for f in formats) or any(e.get("vcodec") not in (None, "none") for e in entries))
    has_audio = bool(info.get("acodec") not in (None, "none") or any(f.get("acodec") not in (None, "none") for f in formats) or any(e.get("acodec") not in (None, "none") for e in entries))
    is_image = (ext in image_exts or bool(entries) and all(str(e.get("ext") or "").lower() in image_exts for e in entries)) and not has_video
    return {
        "title": _clean_text(info.get("title") or info.get("id") or "Media", 180),
        "uploader": _clean_text(info.get("uploader") or info.get("channel") or info.get("creator"), 100),
        "duration": info.get("duration"),
        "extractor": _clean_text(info.get("extractor_key") or info.get("extractor") or "site", 80),
        "ext": ext,
        "has_video": has_video,
        "has_audio": has_audio,
        "is_image": is_image,
        "heights": heights,
        "entries_count": len(entries),
        "is_collection": bool(entries) or info.get("_type") in {"playlist", "multi_video"},
    }


async def _get_info_unlocked(url: str, timeout: int = 8) -> dict:
    host = (urlsplit(url).hostname or "").lower()
    gallery_url = (host == "instagram.com" or host.endswith(".instagram.com")
                   or host == "pinterest.com" or host.endswith(".pinterest.com")
                   or host == "pin.it" or host.endswith(".pin.it"))
    command = [
        sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-warnings",
        "--socket-timeout", "8", "--retries", str(max(0, settings.max_download_attempts - 1)), "--extractor-retries", str(max(0, settings.max_download_attempts - 1)),
        "--dump-single-json", "--skip-download", url,
    ]
    if host in {"youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com"} or host.endswith(".youtube.com"):
        command[command.index("--dump-single-json"):command.index("--dump-single-json")] = ["--remote-components", "ejs:github"]
    if not gallery_url: command.insert(4, "--no-playlist")
    try:
        proc = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(settings.temp_dir),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        if "proc" in locals():
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
        raise DownloadError("The site took too long to respond.") from exc
    except asyncio.CancelledError:
        if "proc" in locals():
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
        raise
    except OSError as exc:
        raise DownloadError("Could not start yt-dlp. Check the container dependencies.") from exc
    if proc.returncode != 0:
        message = _clean_text(stderr.decode("utf-8", "replace")[-1200:], 700)
        if is_youtube_host(host) and is_youtube_bot_check_error(message):
            raise DownloadError("YouTube is temporarily rejecting automated requests from this server. Try again later; this may be a source-side restriction.")
        raise DownloadError(message or "The source did not provide accessible media.")
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DownloadError("The source returned unexpected metadata.") from exc
    if not isinstance(data, dict):
        raise DownloadError("The link did not resolve to a single media item.")
    if data.get("_type") in {"playlist", "multi_video"} and not data.get("entries"):
        raise DownloadError("No media items were returned by the source.")
    return data


async def get_info(url: str, timeout: int = 8) -> dict:
    async with ytdlp_operation():
        return await _get_info_unlocked(url, timeout=timeout)


def make_download_command(url: str, mode: str, out_dir: Path) -> list[str]:
    output_template = str(out_dir / "%(title).100B [%(id)s].%(ext)s")
    host = (urlsplit(url).hostname or "").lower()
    gallery_url = (host == "instagram.com" or host.endswith(".instagram.com")
                   or host == "pinterest.com" or host.endswith(".pinterest.com")
                   or host == "pin.it" or host.endswith(".pin.it"))
    command = [
        sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-warnings",
        "--socket-timeout", "12", "--retries", str(max(1, min(2, settings.max_download_attempts - 1))), "--extractor-retries", str(max(1, min(2, settings.max_download_attempts - 1))),
        "--fragment-retries", str(max(1, min(2, settings.max_download_attempts - 1))), "--concurrent-fragments", "8",
        "--buffer-size", "1M", "--max-filesize", f"{runtime_file_size_mb}M",
        "--newline", "--progress-template",
        "download:DLPROGRESS:%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        "--output", output_template,
    ]
    if host in {"youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com"} or host.endswith(".youtube.com"):
        command[command.index("--newline"):command.index("--newline")] = ["--remote-components", "ejs:github"]
    if not gallery_url: command.insert(4, "--no-playlist")
    else: command += ["--playlist-end", "10"]
    if mode == "audio":
        command += ["--format", "bestaudio/best", "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"]
    elif mode in {"best", "q1080", "q720", "q480"}:
        if mode == "best":
            # Speed-first default: cap at 720p when possible and prefer a single MP4
            # stream to avoid downloading and merging separate video/audio streams.
            selector = "best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]/best/bestvideo*+bestaudio"
        else:
            height = {"q1080": 1080, "q720": 720, "q480": 480}[mode]
            selector = f"best[height<={height}][ext=mp4]/best[height<={height}]/bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
        command += ["--format", selector, "--merge-output-format", "mp4"]
    else:
        # No forced format allows extractors to return original images or gallery media.
        command += ["--format", "best/bestvideo+bestaudio"]
    command.append(url)
    return command


async def _download_media_unlocked(
    url: str,
    mode: str,
    out_dir: Path,
    on_progress: ProgressCallback,
    on_process: ProcessCallback,
    timeout_seconds: int,
    cancel_event: asyncio.Event,
) -> list[Path]:
    if cancel_event.is_set():
        raise DownloadCancelled("Cancelled by user")
    command = make_download_command(url, mode, out_dir)
    proc: asyncio.subprocess.Process | None = None
    start = time.monotonic()
    last_pct = ""
    recent_output: deque[str] = deque(maxlen=30)
    try:
        proc = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=str(out_dir),
        )
        on_process(proc)
        assert proc.stdout is not None
        while True:
            if cancel_event.is_set():
                if proc.returncode is None:
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    if proc.returncode is None:
                        proc.kill()
                    await proc.wait()
                raise DownloadCancelled("Cancelled by user")
            remaining = timeout_seconds - (time.monotonic() - start)
            if remaining <= 0:
                proc.kill()
                await proc.wait()
                raise DownloadError("The download timed out.")
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=min(1.0, remaining))
            except asyncio.TimeoutError:
                continue
            if not raw:
                if proc.returncode is not None:
                    break
                await asyncio.sleep(0.1)
                continue
            line = raw.decode("utf-8", "replace").strip()
            if line:
                recent_output.append(line[-500:])
            if line.startswith("DLPROGRESS:"):
                bits = line[len("DLPROGRESS:"):].split("|", 2)
                percent = re.sub(r"[^0-9.]", "", bits[0]) if bits else ""
                if percent and percent != last_pct:
                    last_pct = percent
                    await on_progress({"percent": percent, "speed": bits[1].strip() if len(bits) > 1 else "", "eta": bits[2].strip() if len(bits) > 2 else ""})
        return_code = await proc.wait()
        if cancel_event.is_set():
            raise DownloadCancelled("Cancelled by user")
        if return_code != 0:
            output_text = "\n".join(recent_output)
            host = (urlsplit(url).hostname or "").lower()
            if is_youtube_host(host) and is_youtube_bot_check_error(output_text):
                raise DownloadError("YouTube is temporarily rejecting automated requests from this server. Try again later; this may be a source-side restriction.")
            raise DownloadError("yt-dlp failed. The media may be unavailable or the selected format may not exist.")
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                if proc.returncode is None:
                    proc.kill()
                await proc.wait()
        raise
    except OSError as exc:
        raise DownloadError("Could not launch the download process.") from exc
    finally:
        on_process(None)
    files = [path for path in out_dir.iterdir() if path.is_file() and path.name not in {".DS_Store"}]
    files = [path for path in files if not path.name.endswith((".part", ".ytdl", ".temp"))]
    files.sort(key=lambda p: p.stat().st_mtime)
    if not files:
        raise DownloadError("The source did not create a media file.")
    for path in files:
        if path.stat().st_size > runtime_file_size_mb * 1024 * 1024:
            raise DownloadError(f"File exceeds {runtime_file_size_mb} MB upload limit.")
    return files[:10]


async def download_media(
    url: str,
    mode: str,
    out_dir: Path,
    on_progress: ProgressCallback,
    on_process: ProcessCallback,
    timeout_seconds: int,
    cancel_event: asyncio.Event,
) -> list[Path]:
    async with ytdlp_operation():
        return await _download_media_unlocked(
            url, mode, out_dir, on_progress, on_process, timeout_seconds, cancel_event
        )


def is_youtube_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"} or host.endswith(".youtube.com")


def is_youtube_bot_check_error(message: str) -> bool:
    lowered = (message or "").lower()
    indicators = (
        "sign in to confirm you’re not a bot",
        "sign in to confirm you're not a bot",
        "confirm you're not a bot",
        "confirm you are not a bot",
        "http error 429",
        "too many requests",
        "temporarily rejecting automated requests",
    )
    return any(marker in lowered for marker in indicators)


def remove_temp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
