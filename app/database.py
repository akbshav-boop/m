"""SQLite-backed storage for MediaMate.

The repository uses one storage API for both bots. SQLite survives process restarts
as long as DATABASE_PATH points at a persistent filesystem or volume. Free hosting
filesystems may be ephemeral; see DEPLOYMENT.md before treating this as production data.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from collections import defaultdict, deque
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
_conn: sqlite3.Connection | None = None
_db_lock = asyncio.Lock()
_db_path: Path | None = None

_DEFAULT_SETTINGS: dict[str, Any] = {
    "maintenance": False,
    "downloads_enabled": True,
    "source_enabled": {},
    "max_file_size_mb": None,
    "max_video_duration_seconds": None,
    "download_cooldown_seconds": None,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _unjson(value: str | None, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _connect() -> sqlite3.Connection:
    global _conn, _db_path
    if _conn is not None:
        return _conn
    raw_path = os.getenv("DATABASE_PATH", "./data/mediamate.sqlite3").strip()
    if raw_path == ":memory:":
        path = Path(":memory:")
    else:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        parent_existed = path.parent.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not parent_existed:
            try: path.parent.chmod(0o700)
            except OSError: pass
    conn = sqlite3.connect(str(path) if str(path) == ":memory:" else path, timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if str(path) != ":memory:":
        try: path.chmod(0o600)
        except OSError: pass
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA secure_delete=ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    _conn, _db_path = conn, path
    return conn


def _schema(conn: sqlite3.Connection) -> None:
    # Older builds persisted every download URL and a separate favorites table.
    # Keep only compact aggregate metrics and support tickets in durable storage.
    legacy_rows = []
    purged_sensitive_data = False
    table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='downloads'").fetchone()
    if table:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(downloads)").fetchall()}
        if "url" in columns:
            purged_sensitive_data = True
            legacy_rows = conn.execute("SELECT status,source,media_type,duration_ms,created_at FROM downloads").fetchall()
            conn.execute("DROP INDEX IF EXISTS idx_downloads_user_created")
            conn.execute("DROP INDEX IF EXISTS idx_downloads_created")
            conn.execute("ALTER TABLE downloads RENAME TO downloads_legacy_mediamate")
    # Purge old per-link history and favorites: these are intentionally not retained.
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='favorites'").fetchone():
        purged_sensitive_data = True
    conn.execute("DROP TABLE IF EXISTS favorites")
    conn.execute("DROP TABLE IF EXISTS history_clears")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        username TEXT,
        language TEXT NOT NULL DEFAULT 'ru',
        accepted INTEGER NOT NULL DEFAULT 0,
        blocked INTEGER NOT NULL DEFAULT 0,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        profile_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen DESC);
    CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);


    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        subject TEXT NOT NULL DEFAULT 'Support request',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        assigned_admin INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_tickets_status_updated ON tickets(status, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_tickets_user_updated ON tickets(telegram_id, updated_at DESC);

    CREATE TABLE IF NOT EXISTS ticket_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
        sender_id INTEGER NOT NULL,
        sender_type TEXT NOT NULL,
        body TEXT NOT NULL,
        file_id TEXT,
        file_type TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket ON ticket_messages(ticket_id, id);

    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(created_at DESC);

    CREATE TABLE IF NOT EXISTS metrics (
        day TEXT PRIMARY KEY,
        new_users INTEGER NOT NULL DEFAULT 0,
        downloads_ok INTEGER NOT NULL DEFAULT 0,
        downloads_failed INTEGER NOT NULL DEFAULT 0,
        download_ms INTEGER NOT NULL DEFAULT 0,
        download_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS source_metrics (
        day TEXT NOT NULL,
        source TEXT NOT NULL,
        downloads_ok INTEGER NOT NULL DEFAULT 0,
        downloads_failed INTEGER NOT NULL DEFAULT 0,
        download_ms INTEGER NOT NULL DEFAULT 0,
        download_count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(day, source)
    );
    CREATE INDEX IF NOT EXISTS idx_source_metrics_day ON source_metrics(day DESC);
    PRAGMA user_version=2;
    """)
    if legacy_rows:
        # Convert old rows into per-day/source counters, then remove all URLs and per-user logs.
        for row in legacy_rows:
            try:
                created = _dt(row["created_at"])
                day = created.date().isoformat() if created else _now().date().isoformat()
                source = str(row["source"] or "unknown")[:80]
                status_col = "downloads_ok" if row["status"] == "success" else "downloads_failed"
                conn.execute("INSERT OR IGNORE INTO source_metrics(day,source) VALUES(?,?)", (day, source))
                conn.execute(f"UPDATE source_metrics SET {status_col}={status_col}+1,download_ms=download_ms+?,download_count=download_count+1 WHERE day=? AND source=?", (max(0, int(row["duration_ms"] or 0)), day, source))
            except (TypeError, ValueError, KeyError):
                continue
        conn.execute("DROP TABLE IF EXISTS downloads_legacy_mediamate")
    # Drop any left-over migration tables from a previously interrupted update.
    conn.execute("DROP TABLE IF EXISTS downloads_legacy_mediamate")
    # Individual download rows are not retained in this release.
    conn.execute("DROP TABLE IF EXISTS downloads")
    conn.execute("DELETE FROM source_metrics WHERE day < ?", ((_now().date() - timedelta(days=365)).isoformat(),))
    conn.execute("DELETE FROM metrics WHERE day < ?", ((_now().date() - timedelta(days=365)).isoformat(),))
    for key, value in _DEFAULT_SETTINGS.items():
        if value is not None:
            conn.execute("INSERT OR IGNORE INTO settings(key,value_json) VALUES(?,?)", (key, _json(value)))
    conn.commit()
    if purged_sensitive_data:
        # Defragment a small legacy DB so dropped link data is not left in free pages/WAL.
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            log.exception("Could not securely compact legacy URL tables")


async def init_database():
    """Open storage, initialize schema and default settings."""
    async with _db_lock:
        conn = _connect()
        _schema(conn)
        log.info("SQLite storage initialized at %s", _db_path or "memory database")


async def close_database():
    global _conn, _db_path
    async with _db_lock:
        if _conn is not None:
            _conn.commit()
            _conn.close()
        _conn = None
        _db_path = None


async def set_default(key: str, value: Any):
    async with _db_lock:
        conn = _connect()
        conn.execute("INSERT OR IGNORE INTO settings(key,value_json) VALUES(?,?)", (key, _json(value)))
        conn.commit()


async def get_setting(key: str, default: Any = None):
    async with _db_lock:
        row = _connect().execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        return _unjson(row["value_json"], default)


async def set_setting(key: str, value: Any):
    async with _db_lock:
        conn = _connect()
        conn.execute("INSERT INTO settings(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (key, _json(value)))
        conn.commit()


async def save_user(uid: int, user: dict, name: str | None = None, username: str | None = None):
    now = _now()
    uid = int(uid)
    profile = dict(user or {})
    profile.pop("last_download", None)
    language = str(profile.get("language", "ru"))
    accepted = bool(profile.get("accepted", False))
    blocked = bool(profile.get("blocked", False))
    safe_name = str(name or profile.get("name") or uid)[:256]
    safe_username = (username if username is not None else profile.get("username"))
    if safe_username is not None:
        safe_username = str(safe_username).lstrip("@")[:64]
    profile.setdefault("first_seen", now.date().isoformat())
    async with _db_lock:
        conn = _connect()
        old = conn.execute("SELECT first_seen, profile_json FROM users WHERE telegram_id=?", (uid,)).fetchone()
        first_seen = old["first_seen"] if old else now.isoformat()
        if old:
            old_profile = _unjson(old["profile_json"], {})
            if not isinstance(old_profile, dict): old_profile = {}
            profile.setdefault("first_seen", old_profile.get("first_seen", now.date().isoformat()))
        conn.execute("""INSERT INTO users(telegram_id,name,username,language,accepted,blocked,first_seen,last_seen,profile_json)
          VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET
          name=excluded.name, username=COALESCE(excluded.username, users.username), language=excluded.language,
          accepted=excluded.accepted, blocked=excluded.blocked, last_seen=excluded.last_seen, profile_json=excluded.profile_json""",
          (uid, safe_name, safe_username, language, int(accepted), int(blocked), first_seen, now.isoformat(), _json(profile)))
        conn.commit()


def _user_row(row: sqlite3.Row) -> dict[str, Any]:
    profile = _unjson(row["profile_json"], {})
    if not isinstance(profile, dict): profile = {}
    profile.setdefault("name", row["name"])
    profile.setdefault("language", row["language"])
    profile.setdefault("accepted", bool(row["accepted"]))
    profile.setdefault("successes", 0)
    profile.setdefault("quality", "best")
    profile.setdefault("format", "video")
    profile.setdefault("offered", False)
    profile["last_download"] = 0.0
    profile["blocked"] = bool(row["blocked"])
    profile["username"] = row["username"]
    profile.setdefault("first_seen", (_dt(row["first_seen"]) or _now()).date().isoformat())
    return profile


async def load_users():
    async with _db_lock:
        rows = _connect().execute("SELECT * FROM users").fetchall()
        return {int(row["telegram_id"]): _user_row(row) for row in rows}


async def record_download(uid: int, url: str, status: str, source: str = "", media_type: str = "", duration_ms: int = 0):
    """Record counters only. The URL and per-user download event are deliberately discarded."""
    now = _now()
    day = now.date().isoformat()
    duration_ms = max(0, int(duration_ms))
    source_key = str(source or "unknown")[:80]
    async with _db_lock:
        conn = _connect()
        conn.execute("INSERT OR IGNORE INTO metrics(day) VALUES(?)", (day,))
        field = "downloads_ok" if status == "success" else "downloads_failed"
        conn.execute(f"UPDATE metrics SET {field}={field}+1, download_ms=download_ms+?, download_count=download_count+1 WHERE day=?", (duration_ms, day))
        conn.execute("INSERT OR IGNORE INTO source_metrics(day,source) VALUES(?,?)", (day, source_key))
        conn.execute(f"UPDATE source_metrics SET {field}={field}+1,download_ms=download_ms+?,download_count=download_count+1 WHERE day=? AND source=?", (duration_ms, day, source_key))
        # Keep only aggregate counters for the last year to bound database growth.
        cutoff = (now.date() - timedelta(days=365)).isoformat()
        conn.execute("DELETE FROM source_metrics WHERE day < ?", (cutoff,))
        conn.execute("DELETE FROM metrics WHERE day < ?", (cutoff,))
        conn.commit()
    return None


async def record_new_user():
    day = _now().date().isoformat()
    async with _db_lock:
        conn = _connect()
        conn.execute("INSERT OR IGNORE INTO metrics(day) VALUES(?)", (day,))
        conn.execute("UPDATE metrics SET new_users=new_users+1 WHERE day=?", (day,))
        conn.commit()


async def stats():
    now = _now()
    cutoff = (now - timedelta(hours=24)).isoformat()
    async with _db_lock:
        conn = _connect()
        users = conn.execute("SELECT COUNT(*) total, COALESCE(SUM(blocked),0) blocked, COALESCE(SUM(CASE WHEN last_seen>=? THEN 1 ELSE 0 END),0) active FROM users", (cutoff,)).fetchone()
        downloads = conn.execute("SELECT COALESCE(SUM(downloads_ok),0) ok, COALESCE(SUM(downloads_failed),0) failed FROM metrics").fetchone()
        tickets = conn.execute("SELECT COUNT(*) n FROM tickets WHERE status IN ('open','in_progress')").fetchone()
        return {"users": int(users["total"]), "blocked": int(users["blocked"]), "ok": int(downloads["ok"]), "failed": int(downloads["failed"]), "open_tickets": int(tickets["n"]), "active_24h": int(users["active"])}


async def recent_users(limit: int = 15):
    async with _db_lock:
        rows = _connect().execute("SELECT * FROM users ORDER BY first_seen DESC LIMIT ?", (max(1, min(100, int(limit))),)).fetchall()
        return [{"telegram_id": int(r["telegram_id"]), "name": r["name"], "username": r["username"], "language": r["language"], "blocked": bool(r["blocked"]), "profile": _unjson(r["profile_json"], {}), "first_seen": _dt(r["first_seen"]), "last_seen": _dt(r["last_seen"])} for r in rows]


async def find_users(query: str, limit: int = 20):
    q = str(query).strip().lstrip("@").casefold()
    if not q: return []
    async with _db_lock:
        rows = _connect().execute("SELECT * FROM users ORDER BY last_seen DESC LIMIT 500").fetchall()
        output = []
        for r in rows:
            matched = int(r["telegram_id"]) == int(q) if q.isdigit() else q in r["name"].casefold() or q in (r["username"] or "").casefold()
            if matched:
                output.append({"telegram_id": int(r["telegram_id"]), "name": r["name"], "username": r["username"], "language": r["language"], "blocked": bool(r["blocked"]), "profile": _unjson(r["profile_json"], {})})
            if len(output) >= max(1, min(100, limit)): break
        return output


async def set_blocked(uid: int, blocked: bool):
    async with _db_lock:
        conn = _connect()
        cur = conn.execute("UPDATE users SET blocked=? WHERE telegram_id=?", (int(bool(blocked)), int(uid)))
        if cur.rowcount:
            row = conn.execute("SELECT profile_json FROM users WHERE telegram_id=?", (int(uid),)).fetchone()
            profile = _unjson(row["profile_json"], {})
            if isinstance(profile, dict):
                profile["blocked"] = bool(blocked)
                conn.execute("UPDATE users SET profile_json=? WHERE telegram_id=?", (_json(profile), int(uid)))
        conn.commit()


async def all_user_ids(language: str | None = None):
    async with _db_lock:
        sql = "SELECT telegram_id FROM users WHERE accepted=1 AND blocked=0"
        args: tuple[Any, ...] = ()
        if language is not None:
            sql += " AND language=?"; args = (language,)
        sql += " ORDER BY telegram_id"
        return [int(r[0]) for r in _connect().execute(sql, args).fetchall()]


async def create_ticket(uid: int, body: str, file_id: str | None = None, file_type: str | None = None):
    now = _now().isoformat()
    uid = int(uid)
    async with _db_lock:
        conn = _connect()
        opened = conn.execute("SELECT COUNT(*) n FROM tickets WHERE telegram_id=? AND status IN ('open','in_progress')", (uid,)).fetchone()["n"]
        if int(opened) >= 2: return None
        cur = conn.execute("INSERT INTO tickets(telegram_id,status,subject,created_at,updated_at) VALUES(?,?,?,?,?)", (uid, "open", "Support request", now, now))
        tid = int(cur.lastrowid)
        conn.execute("INSERT INTO ticket_messages(ticket_id,sender_id,sender_type,body,file_id,file_type,created_at) VALUES(?,?,?,?,?,?,?)", (tid, uid, "user", str(body)[:4000], file_id, file_type, now))
        conn.commit()
        return tid


async def add_ticket_message(tid: int, sender_id: int, sender_type: str, body: str, file_id: str | None = None, file_type: str | None = None):
    now = _now().isoformat()
    async with _db_lock:
        conn = _connect()
        ticket = conn.execute("SELECT telegram_id,status FROM tickets WHERE id=?", (int(tid),)).fetchone()
        if not ticket: return None
        conn.execute("INSERT INTO ticket_messages(ticket_id,sender_id,sender_type,body,file_id,file_type,created_at) VALUES(?,?,?,?,?,?,?)", (int(tid), int(sender_id), str(sender_type)[:20], str(body)[:4000], file_id, file_type, now))
        if sender_type == "admin":
            status = "in_progress" if ticket["status"] == "open" else ticket["status"]
            conn.execute("UPDATE tickets SET updated_at=?, status=?, assigned_admin=? WHERE id=?", (now, status, int(sender_id), int(tid)))
        else:
            conn.execute("UPDATE tickets SET updated_at=? WHERE id=?", (now, int(tid)))
        conn.commit()
        return int(ticket["telegram_id"])


def _ticket_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    user = conn.execute("SELECT name,username FROM users WHERE telegram_id=?", (int(row["telegram_id"]),)).fetchone()
    return {"id": int(row["id"]), "telegram_id": int(row["telegram_id"]), "status": row["status"], "subject": row["subject"], "created_at": _dt(row["created_at"]), "updated_at": _dt(row["updated_at"]), "assigned_admin": row["assigned_admin"], "name": user["name"] if user else str(row["telegram_id"]), "username": user["username"] if user else None}


async def list_tickets(status: str = "open", limit: int = 20):
    async with _db_lock:
        conn = _connect()
        if status == "all": rows = conn.execute("SELECT * FROM tickets ORDER BY updated_at DESC LIMIT ?", (max(1, min(100, int(limit))),)).fetchall()
        else: rows = conn.execute("SELECT * FROM tickets WHERE status=? ORDER BY updated_at DESC LIMIT ?", (status, max(1, min(100, int(limit))))).fetchall()
        return [_ticket_row(conn, row) for row in rows]


async def close_ticket(tid: int):
    async with _db_lock:
        conn = _connect()
        row = conn.execute("SELECT telegram_id FROM tickets WHERE id=?", (int(tid),)).fetchone()
        if not row: return None
        conn.execute("UPDATE tickets SET status='closed', updated_at=? WHERE id=?", (_now().isoformat(), int(tid)))
        conn.commit()
        return {"telegram_id": int(row["telegram_id"])}


async def audit(admin_id: int, action: str, details: dict | None = None):
    async with _db_lock:
        conn = _connect()
        conn.execute("INSERT INTO audit(admin_id,action,details_json,created_at) VALUES(?,?,?,?)", (int(admin_id), str(action)[:120], _json(dict(details or {}))[:10000], _now().isoformat()))
        conn.execute("DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT 5000)")
        conn.commit()


async def report_rows(days: int):
    today = _now().date()
    start = (today - timedelta(days=max(1, int(days)))).isoformat()
    async with _db_lock:
        rows = _connect().execute("SELECT * FROM metrics WHERE day>=? AND day<=? ORDER BY day", (start, today.isoformat())).fetchall()
        out = []
        for r in rows:
            count = int(r["download_count"])
            out.append({"day": date.fromisoformat(r["day"]), "new_users": int(r["new_users"]), "downloads_ok": int(r["downloads_ok"]), "downloads_failed": int(r["downloads_failed"]), "avg_ms": round(int(r["download_ms"]) / count) if count else 0})
        return out


async def set_ticket_status(tid: int, status: str):
    allowed = {"open", "in_progress", "closed"}
    if status not in allowed: raise ValueError("Invalid ticket status")
    async with _db_lock:
        conn = _connect()
        row = conn.execute("SELECT telegram_id FROM tickets WHERE id=?", (int(tid),)).fetchone()
        if not row: return None
        conn.execute("UPDATE tickets SET status=?,updated_at=? WHERE id=?", (status, _now().isoformat(), int(tid)))
        conn.commit()
        return {"telegram_id": int(row["telegram_id"])}


async def recent_downloads(limit: int = 20):
    """Return source/day aggregates only; URLs and per-user download logs are not stored."""
    cutoff = (_now().date() - timedelta(days=7)).isoformat()
    async with _db_lock:
        rows = _connect().execute("SELECT day,source,downloads_ok,downloads_failed,download_ms,download_count FROM source_metrics WHERE day>=? ORDER BY day DESC,download_count DESC LIMIT ?", (cutoff, max(1, min(100, int(limit))))).fetchall()
        output = []
        for r in rows:
            total = int(r["downloads_ok"]) + int(r["downloads_failed"])
            output.append({"id": None, "telegram_id": None, "url": None, "status": f"ok {int(r['downloads_ok'])} / failed {int(r['downloads_failed'])}", "source": r["source"], "media_type": "aggregate", "duration_ms": round(int(r["download_ms"]) / max(1, int(r["download_count"]))), "created_at": datetime.fromisoformat(r["day"] + "T00:00:00+00:00"), "name": "Daily aggregate", "username": None, "total": total})
        return output


async def load_history(limit_per_user: int = 10):
    """No download URLs are restored from the database; history is session-only."""
    return defaultdict(lambda: deque(maxlen=max(1, int(limit_per_user))))


async def source_stats(days: int = 7):
    cutoff = (_now().date() - timedelta(days=max(1, int(days)))).isoformat()
    async with _db_lock:
        rows = _connect().execute("SELECT source,SUM(downloads_ok+downloads_failed) total,SUM(downloads_ok) ok,SUM(downloads_failed) failed,CASE WHEN SUM(download_count)>0 THEN SUM(download_ms)*1.0/SUM(download_count) ELSE 0 END avg_ms FROM source_metrics WHERE day>=? GROUP BY source ORDER BY total DESC LIMIT 20", (cutoff,)).fetchall()
        return [{"source": r["source"] or "unknown", "total": int(r["total"] or 0), "ok": int(r["ok"] or 0), "failed": int(r["failed"] or 0), "avg_ms": round(float(r["avg_ms"] or 0))} for r in rows]


async def recent_audit(limit: int = 20):
    async with _db_lock:
        rows = _connect().execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(100, int(limit))),)).fetchall()
        return [{"id": int(r["id"]), "admin_id": int(r["admin_id"]), "action": r["action"], "details": _unjson(r["details_json"], {}), "created_at": _dt(r["created_at"])} for r in rows]


async def user_tickets(uid: int, limit: int = 10):
    async with _db_lock:
        conn = _connect()
        rows = conn.execute("SELECT * FROM tickets WHERE telegram_id=? ORDER BY updated_at DESC LIMIT ?", (int(uid), max(1, min(100, int(limit))))).fetchall()
        return [_ticket_row(conn, row) for row in rows]


async def get_ticket(ticket_id: int):
    async with _db_lock:
        conn = _connect()
        row = conn.execute("SELECT * FROM tickets WHERE id=?", (int(ticket_id),)).fetchone()
        if not row: return None
        result = _ticket_row(conn, row)
        messages = conn.execute("SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY id", (int(ticket_id),)).fetchall()
        result["messages"] = [{"id": int(m["id"]), "ticket_id": int(m["ticket_id"]), "sender_id": int(m["sender_id"]), "sender_type": m["sender_type"], "body": m["body"], "file_id": m["file_id"], "file_type": m["file_type"], "created_at": _dt(m["created_at"])} for m in messages]
        return result


async def clear_user_history(uid: int):
    """Compatibility no-op: user link history exists only in process memory."""
    return None


async def add_favorite(uid: int, url: str, title: str = "") -> bool:
    """Saved media links are disabled by design; never persist the supplied URL."""
    return False


async def list_favorites(uid: int, limit: int = 20):
    return []


async def remove_favorite(uid: int, favorite_id: int) -> bool:
    return False


async def storage_info() -> dict[str, Any]:
    """Report storage footprint without inspecting or returning ticket contents or URLs."""
    async with _db_lock:
        conn = _connect()
        path = _db_path
        counts = {}
        for table in ("users", "tickets", "ticket_messages", "settings", "audit", "metrics", "source_metrics"):
            try:
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                counts[table] = 0
        try:
            size = path.stat().st_size if path is not None and str(path) != ":memory:" and path.exists() else 0
        except OSError:
            size = 0
        return {"path": str(path) if path else "unknown", "size_bytes": size, "rows": counts, "policy": "No per-download URLs or favorites are persisted. Support tickets and replies are persistent."}


async def backup_database(destination: str | Path) -> Path:
    """Create a consistent SQLite backup at destination using SQLite's online backup API."""
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    async with _db_lock:
        source = _connect()
        output = sqlite3.connect(target)
        try:
            source.backup(output)
            output.commit()
        finally:
            output.close()
        try: target.chmod(0o600)
        except OSError: pass
    return target
