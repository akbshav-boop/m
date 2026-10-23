import asyncio
import importlib
import sqlite3
from pathlib import Path


def _db(monkeypatch, path):
    monkeypatch.setenv("DATABASE_PATH", str(path))
    import app.database as database
    return importlib.reload(database)


def test_sqlite_storage_survives_reopening_without_download_urls(monkeypatch, tmp_path):
    path = tmp_path / "mediamate.sqlite3"
    db = _db(monkeypatch, path)

    async def run():
        await db.init_database()
        await db.save_user(123, {"language":"ru","accepted":True,"name":"Tester","successes":0}, "Tester", "tester")
        await db.record_new_user()
        await db.record_download(123, "https://example.com/a.mp4?token=secret", "success", "other", "video", 1200)
        await db.record_download(123, "https://example.com/b.mp4?private=1", "failed", "other", "video", 500)
        await db.set_setting("downloads_enabled", False)
        assert (await db.stats())["users"] == 1
        assert (await db.stats())["ok"] == 1
        assert (await db.stats())["failed"] == 1
        assert await db.all_user_ids() == [123]
        assert await db.get_setting("downloads_enabled") is False
        assert (await db.load_users())[123]["language"] == "ru"
        # The database returns compact aggregates, never per-user URLs.
        aggregates = await db.recent_downloads(5)
        assert len(aggregates) == 1
        assert aggregates[0]["url"] is None
        assert (await db.source_stats(7))[0]["total"] == 2
        assert (await db.load_history(10)).get(123) is None
        await db.clear_user_history(123)
        assert await db.add_favorite(123, "https://example.com/a.mp4", "example.com") is False
        assert await db.list_favorites(123) == []
        info = await db.storage_info()
        assert info["rows"]["users"] == 1
        assert info["rows"]["metrics"] == 1
        backup_path = tmp_path / "backup.sqlite3"
        await db.backup_database(backup_path)
        assert backup_path.exists() and backup_path.stat().st_size > 0
        # There is no individual download log table or favorites table.
        names = {r[0] for r in db._connect().execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "downloads" not in names
        assert "favorites" not in names
        await db.close_database()

    asyncio.run(run())
    db = importlib.reload(db)

    async def verify():
        await db.init_database()
        assert (await db.stats())["users"] == 1
        assert (await db.stats())["ok"] == 1
        assert (await db.stats())["failed"] == 1
        assert await db.get_setting("downloads_enabled") is False
        assert await db.list_favorites(123) == []
        await db.close_database()

    asyncio.run(verify())


def test_migration_removes_legacy_download_and_favorite_urls(monkeypatch, tmp_path):
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
            url TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
            media_type TEXT NOT NULL DEFAULT '', duration_ms INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_downloads_user_created ON downloads(telegram_id, created_at DESC);
        CREATE INDEX idx_downloads_created ON downloads(created_at DESC);
        CREATE TABLE favorites (id INTEGER PRIMARY KEY, telegram_id INTEGER, url TEXT NOT NULL, title TEXT, created_at TEXT);
    """)
    secret_url = "https://example.com/old-link.mp4?token=legacy-secret"
    conn.execute("INSERT INTO downloads(telegram_id,url,status,source,media_type,duration_ms,created_at) VALUES(1,?,?,?,?,?,?)", (secret_url, "success", "youtube", "video", 1000, "2026-10-24T00:00:00+00:00"))
    conn.execute("INSERT INTO favorites VALUES(1,1,?,?,?)", (secret_url, "private favorite", "2026-10-24T00:00:00+00:00"))
    conn.commit()
    conn.close()
    db = _db(monkeypatch, path)

    async def run():
        await db.init_database()
        conn = db._connect()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "downloads" not in tables
        assert "favorites" not in tables
        assert (await db.source_stats(7))[0]["source"] == "youtube"
        await db.close_database()

    asyncio.run(run())
    raw = path.read_bytes()
    assert secret_url.encode() not in raw
    assert b"legacy-secret" not in raw


def test_sqlite_support_ticket_lifecycle(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path / "tickets.sqlite3")

    async def run():
        await db.init_database()
        await db.save_user(321, {"language":"en","accepted":True,"name":"Support User"}, "Support User", None)
        ticket_id = await db.create_ticket(321, "Help")
        assert ticket_id is not None
        assert (await db.stats())["open_tickets"] == 1
        assert await db.add_ticket_message(ticket_id, 999, "admin", "Reply") == 321
        data = await db.get_ticket(ticket_id)
        assert data["messages"][-1]["body"] == "Reply"
        assert await db.close_ticket(ticket_id) == {"telegram_id":321}
        assert (await db.stats())["open_tickets"] == 0
        await db.close_database()

    asyncio.run(run())
