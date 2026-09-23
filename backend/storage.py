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

    def executemany(self, sql, rows):
        if not rows:
            return
        if self.postgres:
            with self.conn.cursor() as cursor:
                cursor.executemany(sql.replace('?', '%s'), rows)
        else:
            self.conn.executemany(sql, rows)


def initialize():
    with db() as s:
        for sql in [
            'CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, company TEXT NOT NULL, password TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, account TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL)',
            'CREATE TABLE IF NOT EXISTS states (account TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, account TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, account TEXT NOT NULL, action TEXT NOT NULL, created TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS login_limits (username TEXT PRIMARY KEY, attempts INTEGER NOT NULL, reset_at DOUBLE PRECISION NOT NULL)',
            'CREATE TABLE IF NOT EXISTS carbon_links (account TEXT PRIMARY KEY, token TEXT NOT NULL, site_map TEXT NOT NULL DEFAULT \'{}\', last_sync TEXT, last_count INTEGER)',
            # Measures and actuals as rows (see inventory.py); the rest of the state stays in states.body.
            'CREATE TABLE IF NOT EXISTS state_rows (account TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, seq INTEGER NOT NULL, year INTEGER, body TEXT NOT NULL, PRIMARY KEY(account,kind,id))',
            'CREATE INDEX IF NOT EXISTS state_rows_order ON state_rows(account,kind,seq)',
            'CREATE TABLE IF NOT EXISTS year_closures (account TEXT NOT NULL, year INTEGER NOT NULL, closed_at TEXT NOT NULL, closed_by TEXT NOT NULL, PRIMARY KEY(account,year))',
            'CREATE TABLE IF NOT EXISTS state_backups (account TEXT NOT NULL, created TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS state_uploads (account TEXT NOT NULL, batch TEXT NOT NULL, part INTEGER NOT NULL, created DOUBLE PRECISION NOT NULL, body TEXT NOT NULL, PRIMARY KEY(account,batch,part))',
        ]:
            s.execute(sql)
        _ensure_column(s, 'accounts', 'role', "role TEXT NOT NULL DEFAULT 'client'")
        _ensure_column(s, 'accounts', 'active', 'active BOOLEAN NOT NULL DEFAULT TRUE')
        _ensure_column(s, 'accounts', 'created', "created TEXT NOT NULL DEFAULT ''")
        _ensure_column(s, 'sessions', 'impersonated_by', 'impersonated_by TEXT')


def _column_exists(s, table, column):
    if s.postgres:
        row = s.execute('SELECT 1 FROM information_schema.columns WHERE table_name=? AND column_name=?', (table, column)).fetchone()
    else:
        row = next((r for r in s.execute(f'PRAGMA table_info({table})').fetchall() if r[1] == column), None)
    return bool(row)


def _ensure_column(s, table, column, coldef):
    if not _column_exists(s, table, column):
        s.execute(f'ALTER TABLE {table} ADD COLUMN {coldef}')
