"""SQLite persistence. Each operation uses its own connection."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DEFAULT_CATEGORIES = ["Books", "Articles", "Music", "Videos", "Movies", "Series", "Advice", "Events", "Appointments"]


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.images = self.root / "images"
        self.images.mkdir(exist_ok=True, mode=0o700)
        self.db = self.root / "collection.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS categories (name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, created REAL NOT NULL, comment TEXT NOT NULL,
                    images TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                    generation INTEGER NOT NULL DEFAULT 0, proposals TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS entries (
                    id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL, images TEXT NOT NULL
                );
            """)
            if not db.execute("SELECT 1 FROM categories LIMIT 1").fetchone():
                db.executemany("INSERT INTO categories VALUES (?)", [(c,) for c in DEFAULT_CATEGORIES])
            # An interrupted request is retried, never silently lost.
            db.execute("UPDATE jobs SET status='waiting', message='Server restarted; queued again' WHERE status='processing'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def config(self):
        with self.connect() as db:
            return {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM config")}

    def set_config(self, values):
        with self.connect() as db:
            db.executemany("INSERT INTO config VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           [(k, json.dumps(v)) for k, v in values.items()])

    def categories(self):
        with self.connect() as db:
            return [r[0] for r in db.execute("SELECT name FROM categories ORDER BY name COLLATE NOCASE")]

    def snapshot(self):
        with self.connect() as db:
            jobs = []
            for row in db.execute("SELECT * FROM jobs ORDER BY created DESC"):
                job = dict(row)
                job["images"] = json.loads(job["images"])
                job["proposals"] = json.loads(job["proposals"])
                jobs.append(job)
            entries = []
            for row in db.execute("SELECT * FROM entries ORDER BY created DESC"):
                entry = dict(row)
                entry["data"] = json.loads(entry["data"])
                entry["images"] = json.loads(entry["images"])
                entries.append(entry)
        return {"jobs": jobs, "entries": entries, "categories": self.categories()}

    def cleanup_images(self):
        # Called only while the application's mutation lock is held.
        snapshot = self.snapshot()
        keep = {i for obj in snapshot["jobs"] + snapshot["entries"] for i in obj["images"]}
        for file in self.images.iterdir():
            if file.name not in keep:
                file.unlink()

    def session(self, sid):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
            row = db.execute("SELECT * FROM sessions WHERE id=? AND expires>?", (sid, time.time())).fetchone()
            return dict(row) if row else None
