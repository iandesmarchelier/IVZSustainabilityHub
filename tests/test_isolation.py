"""Tenant isolation.

On PostgreSQL (HUB_TEST_ISOLATION_URL, a disposable database named *test*: its tables are dropped) every test of
test_backend runs again with row-level security on, plus checks that the database itself keeps each
account's rows apart. Without PostgreSQL, a check of the code: only the login, the administrator
and the maintenance scripts may open a connection that is not scoped to one account.
"""
import ast
import os
import unittest
from pathlib import Path

from backend import storage
from backend.storage import SYSTEM, db
from tests import test_backend as base

URL = os.getenv('HUB_TEST_ISOLATION_URL')
ACCOUNT_TABLES = ("SELECT DISTINCT table_name FROM information_schema.columns "
                  "WHERE table_schema=current_schema() AND column_name='account'")


@unittest.skipUnless(URL, 'HUB_TEST_ISOLATION_URL no configurada')
class PostgresTests(base.DemoTests):
    def use_database(self):
        os.environ['DATABASE_URL'] = URL
        with db(SYSTEM) as s:
            if 'test' not in s.conn.info.dbname:
                raise RuntimeError('HUB_TEST_ISOLATION_URL debe ser una base de prueba descartable (con «test» en el nombre).')
            for r in s.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()').fetchall():
                s.execute(f'DROP TABLE {r["tablename"]} CASCADE')

    def account_tables(self):
        with db(SYSTEM) as s:
            return sorted(r['table_name'] for r in s.execute(ACCOUNT_TABLES).fetchall())

    def fill_both_accounts(self):
        """A row for each account in every table that has an account column."""
        for user in ('one', 'two'):
            self.login(user)
            self.assertEqual(self.save().status_code, 200)
            self.assertEqual(self.client.post('/api/reports', headers=self.headers, json={'year': 2026}).status_code, 200)
            self.assertEqual(self.client.post('/api/closures', headers=self.headers, json={'year': 2024}).status_code, 200)
            self.assertEqual(self.client.post('/api/state/upload', headers=self.headers,
                                              json={'batch': 'batch-' + user, 'part': 0, 'changes': {}}).status_code, 200)
        with db(SYSTEM) as s:
            for user in ('one', 'two'):
                s.execute("INSERT INTO carbon_links (account, token) VALUES (?, ?)", (user, 'ivzc_' + user))
                s.execute('INSERT INTO state_backups VALUES (?, ?, 1, ?)', (user, 'x', '{}'))
            for table in self.account_tables():
                found = {r['account'] for r in s.execute(f'SELECT DISTINCT account FROM {table}').fetchall()}
                self.assertEqual(found, {'one', 'two'}, f'{table}: agregá filas de prueba para la tabla nueva')

    def test_every_account_table_has_row_level_security(self):
        with db(SYSTEM) as s:
            secured = {r['relname'] for r in s.execute(
                'SELECT c.relname FROM pg_class c JOIN pg_policies p ON p.tablename=c.relname AND p.schemaname=current_schema() '
                'WHERE c.relnamespace=current_schema()::regnamespace AND c.relrowsecurity').fetchall()}
            role = s.execute('SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=?', (storage.TENANT_ROLE,)).fetchone()
        tables = set(self.account_tables()) | {'accounts'}
        self.assertGreaterEqual(len(tables), 10)
        self.assertEqual(tables - secured, set())
        self.assertEqual(dict(role), {'rolsuper': False, 'rolbypassrls': False})
        self.assertEqual(self.client.get('/health').json()['isolation'], 'row-level-security')

    def test_a_query_without_the_account_filter_sees_only_its_own_rows(self):
        self.fill_both_accounts()
        for user in ('one', 'two'):
            with db(user) as s:
                for table in self.account_tables():
                    with self.subTest(user=user, table=table):
                        self.assertEqual({r['account'] for r in s.execute(f'SELECT account FROM {table}').fetchall()}, {user})
                self.assertEqual([r['id'] for r in s.execute('SELECT id FROM accounts').fetchall()], [user])
        with db('nobody') as s:
            for table in self.account_tables() + ['accounts']:
                self.assertEqual(s.execute(f'SELECT COUNT(*) AS n FROM {table}').fetchone()['n'], 0, table)

    def test_an_account_cannot_write_the_rows_of_another(self):
        import psycopg
        self.fill_both_accounts()
        with db(SYSTEM) as s:
            before = {t: s.execute(f"SELECT COUNT(*) AS n FROM {t} WHERE account='two'").fetchone()['n'] for t in self.account_tables()}
        with db('one') as s:
            for table in self.account_tables():
                self.assertEqual(s.execute(f"DELETE FROM {table} WHERE account='two'").rowcount, 0, table)
            self.assertEqual(s.execute("UPDATE accounts SET company='x' WHERE id='two'").rowcount, 0)
        for sql in ("INSERT INTO events (id,account,action,created) VALUES ('intruso', 'two', 'x', 'x')",  # a row for another account
                    "UPDATE reports SET account='two'"):                    # moving its own rows to another
            with self.subTest(sql=sql), self.assertRaises(psycopg.errors.InsufficientPrivilege), db('one') as s:
                s.execute(sql)
        with db(SYSTEM) as s:
            after = {t: s.execute(f"SELECT COUNT(*) AS n FROM {t} WHERE account='two'").fetchone()['n'] for t in self.account_tables()}
            self.assertEqual(s.execute("SELECT company FROM accounts WHERE id='two'").fetchone()['company'], 'two')
        self.assertEqual(after, before)

    def test_a_client_connection_reaches_only_the_protected_tables(self):
        import psycopg
        with self.assertRaises(psycopg.errors.InsufficientPrivilege), db('one') as s:
            s.execute('SELECT * FROM login_limits')
        with self.assertRaises(psycopg.errors.InsufficientPrivilege), db('one') as s:
            s.execute('DROP TABLE events')

    def test_the_account_scope_ends_with_its_transaction(self):
        # Neon's pooler hands the same server connection to other requests between transactions.
        import psycopg
        with psycopg.connect(URL) as conn:
            owner = conn.execute('SELECT current_user').fetchone()[0]
            conn.commit()
            storage._enter(conn, 'one')
            self.assertEqual(conn.execute("SELECT current_user, current_setting('ivz.account')").fetchone(), (storage.TENANT_ROLE, 'one'))
            conn.commit()
            self.assertEqual(conn.execute("SELECT current_user, current_setting('ivz.account', true)").fetchone(), (owner, ''))


class SystemScopeTests(unittest.TestCase):
    """db(SYSTEM) skips the account's row-level security; keep it where no single account applies."""
    ROOT = Path(__file__).resolve().parent.parent / 'backend'
    ALLOWED = {'app.py': {'bootstrap_admin', 'account', 'login', 'logout', 'admin_return', 'health'},
               'storage.py': {'initialize'}, 'manage.py': {'main'}, 'demo.py': {'main'}, 'unsplit.py': {'main'}}
    ADMIN_CHECKS = {'require_admin', 'admin_carbon_target'}

    def test_system_connections_are_only_for_login_admin_and_scripts(self):
        found = []
        for path in sorted(self.ROOT.glob('*.py')):
            for fn in ast.walk(ast.parse(path.read_text(encoding='utf8'))):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
                if not any(c.func.id == 'db' and c.args and isinstance(c.args[0], ast.Name) and c.args[0].id == 'SYSTEM' for c in calls):
                    continue
                found.append(fn.name)
                admin = any(c.func.id in self.ADMIN_CHECKS for c in calls)
                self.assertTrue(admin or fn.name in self.ALLOWED.get(path.name, ()),
                                f'{path.name}:{fn.lineno} {fn.name} usa db(SYSTEM) sin ser login, administración ni un script')
        self.assertIn('account', found)


if __name__ == '__main__':
    unittest.main()
