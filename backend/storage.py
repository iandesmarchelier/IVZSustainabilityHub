"""Small persistence adapter: local SQLite or hosted PostgreSQL.

Tenant isolation. Every connection says whose data it works on: db(account_id) for a client's
request, db(SYSTEM) for login, the administrator and maintenance scripts. On PostgreSQL,
db(account_id) runs its transaction as TENANT_ROLE with ivz.account set to that id, and
row-level security on every table with an account column (and on accounts itself) hides and
refuses the other accounts' rows, even if a query forgets its WHERE account=?. db(SYSTEM) runs
as the connecting role, which owns the tables and is not subject to the policies.
SQLite (local development and tests) has no row-level security; the argument is required anyway.
"""
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SYSTEM = object()
TENANT_ROLE = 'ivz_hub_tenant'
# Whether the connecting role can switch to TENANT_ROLE; None until checked in this process.
_tenant_role = {'ready': None}


@contextmanager
def db(account):
    if account is not SYSTEM and not (isinstance(account, str) and account):
        raise ValueError('db() needs the account id, or SYSTEM')
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
        if url and account is not SYSTEM:
            _enter(conn, account)
        yield Session(conn, bool(url))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _enter(conn, account):
    """Scope the transaction to one account. Both settings are transaction-local (SET LOCAL), so a
    pooled connection (Neon's pgbouncer) never carries them into someone else's transaction."""
    if _tenant_role['ready'] is None:
        _tenant_role['ready'] = bool(conn.execute(
            'SELECT 1 FROM pg_roles r WHERE r.rolname=%s AND pg_has_role(current_user, r.oid, %s)',
            (TENANT_ROLE, _switch(conn))).fetchone())
        if not _tenant_role['ready']:
            logging.getLogger(__name__).error('Row-level security is NOT enforced: %s is missing or cannot be used.', TENANT_ROLE)
    if _tenant_role['ready']:
        conn.execute("SELECT set_config('role', %s, true), set_config('ivz.account', %s, true)", (TENANT_ROLE, account))
    else:
        conn.execute("SELECT set_config('ivz.account', %s, true)", (account,))


def isolated(s):
    """Whether client connections are confined by row-level security; /health reports it."""
    if not s.postgres:
        return False
    role = s.execute('SELECT pg_has_role(current_user, oid, ?) AS ok FROM pg_roles WHERE rolname=?', (_switch(s.conn), TENANT_ROLE)).fetchone()
    exposed = s.execute("SELECT 1 FROM information_schema.columns c JOIN pg_class t ON t.relname=c.table_name "
                        "AND t.relnamespace=current_schema()::regnamespace WHERE c.table_schema=current_schema() "
                        "AND c.column_name='account' AND NOT t.relrowsecurity").fetchone()
    return bool(role and role['ok']) and not exposed


def _switch(conn):
    # Since PostgreSQL 16 a role's creator holds ADMIN on it but may still lack SET, which SET ROLE needs.
    return 'SET' if conn.info.server_version >= 160000 else 'MEMBER'


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
    with db(SYSTEM) as s:
        # Serialize schema changes across serverless instances starting at once.
        if s.postgres:
            s.execute('SELECT pg_advisory_xact_lock(8347022)')
        for sql in [
            'CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, company TEXT NOT NULL, password TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, account TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL)',
            'CREATE TABLE IF NOT EXISTS states (account TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, account TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, account TEXT NOT NULL, action TEXT NOT NULL, created TEXT NOT NULL)',
            'CREATE INDEX IF NOT EXISTS events_account ON events(account,created)',
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
        # Sections and integrations an administrator switched on or off for the account (backend/features.py).
        _ensure_column(s, 'accounts', 'settings', "settings TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(s, 'sessions', 'impersonated_by', 'impersonated_by TEXT')
        if s.postgres:
            isolate(s, 'accounts')


def isolate(s, accounts_table):
    """Row-level security for every table with an account column, found in the schema so that a new
    table is covered without being listed, plus the accounts table on its id. Only changes what is
    missing: ALTER TABLE locks the table, and this runs on every cold start."""
    tables = {r['table_name']: 'account' for r in s.execute(
        "SELECT DISTINCT table_name FROM information_schema.columns WHERE table_schema=current_schema() AND column_name='account'").fetchall()}
    tables[accounts_table] = 'id'
    secured = {r['relname'] for r in s.execute(
        'SELECT relname FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relrowsecurity').fetchall()}
    policies = {r['tablename'] for r in s.execute(
        "SELECT tablename FROM pg_policies WHERE schemaname=current_schema() AND policyname='tenant'").fetchall()}
    for table, column in sorted(tables.items()):
        if table not in policies:
            # USING also checks inserted and updated rows: an account cannot write rows for another.
            s.execute(f"CREATE POLICY tenant ON {table} USING ({column} = current_setting('ivz.account', true))")
        if table not in secured:
            s.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
    # The table owner (the connecting role) is exempt from the policies, so requests switch to a role
    # that is not. It may only read and write the protected tables.
    s.execute('SAVEPOINT tenant_role')
    try:
        if not s.execute('SELECT 1 FROM pg_roles WHERE rolname=?', (TENANT_ROLE,)).fetchone():
            s.execute(f'CREATE ROLE {TENANT_ROLE} NOLOGIN NOBYPASSRLS')
        if not s.execute('SELECT pg_has_role(current_user, ?, ?) AS ok', (TENANT_ROLE, _switch(s.conn))).fetchone()['ok']:
            s.execute(f'GRANT {TENANT_ROLE} TO CURRENT_USER')
        missing = [r['relname'] for r in s.execute(
            'SELECT relname FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relname=ANY(?) AND NOT ('
            + ' AND '.join(f"has_table_privilege(?, oid, '{p}')" for p in ('SELECT', 'INSERT', 'UPDATE', 'DELETE')) + ') ORDER BY relname',
            (sorted(tables), *[TENANT_ROLE] * 4)).fetchall()]
        if missing:
            s.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(missing)} TO {TENANT_ROLE}")
        s.execute('RELEASE SAVEPOINT tenant_role')
        _tenant_role['ready'] = True
    except Exception:
        s.execute('ROLLBACK TO SAVEPOINT tenant_role')
        _tenant_role['ready'] = False
        logging.getLogger(__name__).exception(
            'Row-level security is NOT enforced: the database role cannot create or use %s.', TENANT_ROLE)


def _column_exists(s, table, column):
    if s.postgres:
        row = s.execute('SELECT 1 FROM information_schema.columns WHERE table_name=? AND column_name=?', (table, column)).fetchone()
    else:
        row = next((r for r in s.execute(f'PRAGMA table_info({table})').fetchall() if r[1] == column), None)
    return bool(row)


def _ensure_column(s, table, column, coldef):
    if not _column_exists(s, table, column):
        s.execute(f'ALTER TABLE {table} ADD COLUMN {coldef}')
