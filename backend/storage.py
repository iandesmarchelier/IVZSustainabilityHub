"""Small persistence adapter: local SQLite or hosted PostgreSQL."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def db():
    url = os.getenv('DATABASE_URL')
    if url:
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(url, row_factory=dict_row)
    else:
        if os.getenv('VERCEL'):
            raise RuntimeError('DATABASE_URL is required on Vercel')
        path = Path(os.getenv('SQLITE_PATH', 'data/ivz.sqlite3'))
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=20)
        conn.row_factory = sqlite3.Row
    try:
        yield Session(conn, bool(url))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class Session:
    def __init__(self, conn, postgres):
        self.conn, self.postgres = conn, postgres

    def execute(self, sql, args=()):
        return self.conn.execute(sql.replace('?', '%s') if self.postgres else sql, args)


def initialize():
    with db() as s:
        for sql in [
            'CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, company TEXT NOT NULL, password TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, account TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL)',
            'CREATE TABLE IF NOT EXISTS states (account TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, account TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, account TEXT NOT NULL, action TEXT NOT NULL, created TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS login_limits (username TEXT PRIMARY KEY, attempts INTEGER NOT NULL, reset_at DOUBLE PRECISION NOT NULL)',
        ]:
            s.execute(sql)
