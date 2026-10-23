"""Process-local runtime state; the free build intentionally resets it on restart."""
from collections import defaultdict, deque
USERS = {}          # telegram_id -> profile/preferences, RAM only
PENDING = {}        # telegram_id -> pending link metadata, RAM only
ACTIVE = {}         # telegram_id -> cancellation event, RAM only
ACTIVE_TASKS = {}    # telegram_id -> cancellable download task, RAM only
ACTIVE_PROCESSES = {} # telegram_id -> active yt-dlp subprocess, RAM only
ACTIVE_STATUS = {} # telegram_id -> editable Telegram status message, RAM only
CHECKING_TASKS = {}  # telegram_id -> metadata lookup task, RAM only
HISTORY = defaultdict(lambda: deque(maxlen=10))
BLOCKED = set()
STATS = {'downloads_ok': 0, 'downloads_failed': 0, 'users_seen': 0}
ADMIN_SESSIONS = set()
