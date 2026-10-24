"""Optional, local-only quota history. No prompts, credentials, or raw payloads."""
import sqlite3
import time
from pathlib import Path


class QuotaHistory:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS readings (at INTEGER, source TEXT, window TEXT, remaining REAL, resets_at INTEGER)')

    def connect(self):
        return sqlite3.connect(self.path)

    def record(self, source, bucket):
        rows = []
        for name in ('primary', 'secondary'):
            item = bucket.get(name)
            used = item.get('usedPercent') if isinstance(item, dict) else None
            if isinstance(used, (int, float)) and not isinstance(used, bool):
                rows.append((int(time.time()), source, name, 100 - used, item.get('resetsAt')))
        with self.connect() as db:
            db.executemany('INSERT INTO readings VALUES (?, ?, ?, ?, ?)', rows)
            db.execute('DELETE FROM readings WHERE at < ?', (int(time.time()) - 30 * 86400,))

    def clear(self):
        with self.connect() as db:
            db.execute('DELETE FROM readings')
