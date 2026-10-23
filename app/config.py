from __future__ import annotations
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

def _int(name: str, default: int, minimum: int = 1) -> int:
    try: value = int(os.getenv(name, str(default)).strip())
    except ValueError as exc: raise ValueError(f"{name} must be an integer") from exc
    if value < minimum: raise ValueError(f"{name} must be >= {minimum}")
    return value

@dataclass(frozen=True, slots=True)
class Settings:
    user_bot_token: str
    admin_bot_token: str
    admin_ids: frozenset[int]
    admin_access_code: str
    support_username: str
    channel_id: str
    channel_url: str
    telegram_api_base_url: str
    max_video_duration: int
    max_file_size_mb: int
    download_timeout_seconds: int
    download_cooldown_seconds: int
    max_download_attempts: int
    max_global_downloads: int
    subscription_offer_interval_hours: int
    log_level: str
    temp_dir: Path
    @classmethod
    def from_env(cls):
        ids_raw = os.getenv('ADMIN_IDS', '').strip()
        try: ids = frozenset(int(x.strip()) for x in ids_raw.split(',') if x.strip())
        except ValueError as exc: raise ValueError('ADMIN_IDS must be comma-separated Telegram numeric IDs') from exc
        obj = cls(
            user_bot_token=(os.getenv('USER_BOT_TOKEN') or os.getenv('BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN') or '').strip(),
            admin_bot_token=os.getenv('ADMIN_BOT_TOKEN', '').strip(), admin_ids=ids,
            admin_access_code=os.getenv('ADMIN_ACCESS_CODE', '').strip(),
            support_username=os.getenv('SUPPORT_USERNAME', 'suportakbshav').strip().lstrip('@'),
            channel_id=os.getenv('CHANNEL_ID', '@akbshav_channel').strip(),
            channel_url=os.getenv('CHANNEL_URL', 'https://t.me/akbshav_channel').strip(),
            telegram_api_base_url=os.getenv('TELEGRAM_API_BASE_URL', '').strip().rstrip('/'),
            max_video_duration=_int('MAX_VIDEO_DURATION', 3600),
            max_file_size_mb=_int('MAX_FILE_SIZE_MB', 45),
            download_timeout_seconds=_int('DOWNLOAD_TIMEOUT_SECONDS', 900),
            download_cooldown_seconds=_int('DOWNLOAD_COOLDOWN_SECONDS', 10, 0),
            max_download_attempts=_int('MAX_DOWNLOAD_ATTEMPTS', 3),
            max_global_downloads=_int('MAX_GLOBAL_DOWNLOADS', 2),
            subscription_offer_interval_hours=_int('SUBSCRIPTION_OFFER_INTERVAL_HOURS', 168),
            log_level=os.getenv('LOG_LEVEL', 'INFO').upper(),
            temp_dir=Path(os.getenv('TEMP_DIR', str(Path(tempfile.gettempdir()) / 'mediamate'))),
        )
        if not obj.user_bot_token: raise ValueError('USER_BOT_TOKEN is required')
        if not obj.admin_bot_token: raise ValueError('ADMIN_BOT_TOKEN is required')
        if not obj.admin_ids: raise ValueError('ADMIN_IDS must contain at least one numeric Telegram ID')
        if len(obj.admin_access_code) < 12: raise ValueError('ADMIN_ACCESS_CODE must be at least 12 characters')
        max_allowed = 2000 if obj.telegram_api_base_url else 45
        if not 1 <= obj.max_file_size_mb <= max_allowed: raise ValueError(f'MAX_FILE_SIZE_MB must be between 1 and {max_allowed}; set TELEGRAM_API_BASE_URL to enable local Bot API uploads above 45 MB')
        return obj
settings = Settings.from_env()
