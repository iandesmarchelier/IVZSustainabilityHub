import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import app
from backend.storage import db
from backend.security import hash_password
from backend.metrics import compute, validate_state


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['SQLITE_PATH'] = str(Path(self.tmp.name) / 'test.sqlite')
        os.environ.pop('DATABASE_URL', None)
        os.environ.pop('GEMINI_API_KEY', None)
        self.client = TestClient(app).__enter__()
        self.headers = {'X-IVZ-Request': '1'}
        self.codes = []
        self.mailer = patch('backend.mfa.send_code', lambda email, code: self.codes.append((email, code)))
        self.mailer.start()
        with db() as s:
            for user, role in [('one', 'client'), ('two', 'client'), ('boss', 'admin')]:
                s.execute('INSERT INTO accounts (id,username,company,password,role,active,created,email) VALUES (?,?,?,?,?,?,?,?)',
                          (user, user, user, hash_password('demopassword123'), role, True, '', user + '@example.com'))
        self.state = json.loads(Path('data/seed.json').read_text(encoding='utf8'))

    def tearDown(self):
        self.mailer.stop()
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def password_step(self, user='one'):
        result = self.client.post('/api/login', headers=self.headers, json={'username': user, 'password': 'demopassword123'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), {'mfa': True, 'email': user[:2] + '•••@example.com'})
        self.assertEqual(self.codes[-1][0], user + '@example.com')
        return self.codes[-1][1]

    def verify(self, code):
        return self.client.post('/api/login/verify', headers=self.headers, json={'code': code})

    def login(self, user='one'):
        result = self.verify(self.password_step(user))
        self.assertEqual(result.status_code, 200, result.text)

    def wrong(self, code):
        return str((int(code) + 1) % 1_000_000).zfill(6)

    def test_password_alone_does_not_open_a_session(self):
        code = self.password_step()
        self.assertEqual(self.client.get('/api/me').status_code, 401)
        self.assertEqual(self.client.post('/api/login/verify', json={'code': code}).status_code, 403)  # CSRF header
        result = self.verify(self.wrong(code))
        self.assertEqual(result.status_code, 401)
        self.assertIn('4 intentos', result.json()['detail'])
        self.assertEqual(self.verify(code[:3] + ' ' + code[3:]).status_code, 200)
        self.assertEqual(self.client.get('/api/me').json()['username'], 'one')
        self.assertEqual(self.verify(code).status_code, 410)  # single use

    def test_login_lasts_thirty_days_by_default(self):
        self.login()
        with db() as s:
            expires = s.execute("SELECT expires FROM sessions WHERE account='one'").fetchone()['expires']
        self.assertAlmostEqual(expires - time.time(), 30 * 24 * 3600, delta=60)
        with patch('backend.app.time.time', return_value=time.time() + 29 * 24 * 3600):
            self.assertEqual(self.client.get('/api/me').status_code, 200)
        with patch('backend.app.time.time', return_value=time.time() + 31 * 24 * 3600):
            self.assertEqual(self.client.get('/api/me').status_code, 401)

    def test_code_attempts_are_limited(self):
        code = self.password_step()
        for _ in range(4):
            self.assertEqual(self.verify(self.wrong(code)).status_code, 401)
        self.assertEqual(self.verify(self.wrong(code)).status_code, 410)
        self.assertEqual(self.verify(code).status_code, 410)
        self.assertEqual(self.client.get('/api/me').status_code, 401)

    def test_code_expires_and_resend_is_throttled(self):
        first = self.password_step()
        self.assertEqual(self.client.post('/api/login/resend', headers=self.headers, json={}).status_code, 429)
        later = time.time() + 61
        with patch('backend.app.time.time', return_value=later):
            result = self.client.post('/api/login/resend', headers=self.headers, json={})
            self.assertEqual(result.status_code, 200, result.text)
            second = self.codes[-1][1]
            if second != first:
                self.assertEqual(self.verify(first).status_code, 401)
        with patch('backend.app.time.time', return_value=later + 601):
            self.assertEqual(self.verify(second).status_code, 410)
            self.assertEqual(self.client.post('/api/login/resend', headers=self.headers, json={}).status_code, 410)

    def test_a_code_only_opens_its_own_login(self):
        code_one = self.password_step('one')
        self.client.cookies.clear()
        self.password_step('two')
        self.assertEqual(self.verify(code_one).status_code, 401)

    def test_account_without_email_cannot_log_in(self):
        with db() as s:
            s.execute("UPDATE accounts SET email=NULL WHERE username='one'")
        result = self.client.post('/api/login', headers=self.headers, json={'username': 'one', 'password': 'demopassword123'})
        self.assertEqual(result.status_code, 403)
        self.assertIn('correo', result.json()['detail'])
        self.assertEqual(self.codes, [])

    def test_admin_manages_login_emails(self):
        self.login('boss')
        bad = self.client.post('/api/admin/accounts', headers=self.headers, json={'username': 'acme', 'company': 'ACME', 'email': 'no-es-un-correo'})
        self.assertEqual(bad.status_code, 400)
        created = self.client.post('/api/admin/accounts', headers=self.headers, json={'username': 'acme', 'company': 'ACME', 'email': ' ana@acme.com '})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()['email'], 'ana@acme.com')
        result = self.client.put('/api/admin/accounts/one/email', headers=self.headers, json={'email': 'nueva@example.com'})
        self.assertEqual(result.status_code, 200, result.text)
        emails = {a['username']: a['email'] for a in self.client.get('/api/admin/accounts').json()}
        self.assertEqual(emails['one'], 'nueva@example.com')
        self.assertEqual(emails['acme'], 'ana@acme.com')
        self.client.cookies.clear()
        self.login('two')
        self.assertEqual(self.client.put('/api/admin/accounts/one/email', headers=self.headers, json={'email': 'x@example.com'}).status_code, 403)

    def test_admin_email_bootstrap_never_overwrites(self):
        from backend.app import bootstrap_admin
        with patch.dict(os.environ, {'ADMIN_PASSWORD_HASH': hash_password('demopassword123'), 'ADMIN_EMAIL': 'admin@example.com'}):
            bootstrap_admin()
        with patch.dict(os.environ, {'ADMIN_EMAIL': 'otro@example.com'}):
            bootstrap_admin()
        with db() as s:
            self.assertEqual(s.execute("SELECT email FROM accounts WHERE username='admin'").fetchone()['email'], 'admin@example.com')

    def save(self, revision=0):
        return self.client.put('/api/state', headers=self.headers, json={'revision': revision, 'state': self.state})

    def test_seed_persistence_and_tenant_isolation(self):
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        self.login()
        result = self.save()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.save().status_code, 409)
        self.assertEqual(self.client.get('/api/state').json()['state']['measures'], self.state['measures'])
        self.login('two')
        self.assertIsNone(self.client.get('/api/state').json()['state'])

    def test_report_and_approval_access(self):
        self.login(); self.assertEqual(self.save().status_code, 200)
        response = self.client.post('/api/reports', headers=self.headers, json={'year': 2026})
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertIn('sin IA', report['meta']['mode'])
        report['meta']['status'] = 'Approved'
        response = self.client.put('/api/reports/' + report['id'], headers=self.headers, json=report)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['meta']['status'], 'Approved')
        self.login('two')
        self.assertEqual(self.client.get('/api/reports').json(), [])
        self.assertEqual(self.client.put('/api/reports/' + report['id'], headers=self.headers, json=report).status_code, 404)

    def test_invalid_numeric_and_csrf(self):
        self.login()
        self.assertEqual(self.client.put('/api/state', json={'revision': 0, 'state': self.state}).status_code, 403)
        self.state['measures'][0]['value'] = 'invalid'
        self.assertEqual(self.save().status_code, 422)

    def test_legacy_imported_global_targets_without_group_node(self):
        import asyncio
        from backend.reports import make_report
        self.state['masterData']['locations'] = [l for l in self.state['masterData']['locations'] if l['id'] != 'GRP']
        for location in self.state['masterData']['locations']:
            if location.get('parent') == 'GRP':
                location['parent'] = None
        for target in self.state['targets']:
            target['loc'] = 'GRP'
        report = asyncio.run(make_report(self.state, {'year':2025,'scope':'ALL'}, 'Test'))
        rows = next(s['blocks'][1:] for s in report['sections'] if s['id']=='targets')
        self.assertEqual(len(rows),len(self.state['targets']))
        self.assertTrue(all(any(v is not None for v in t['datasets'][0]['data']) for t in rows))
        older = asyncio.run(make_report(self.state, {'year':2020,'scope':'ALL'}, 'Test'))
        self.assertEqual(rows,next(s['blocks'][1:] for s in older['sections'] if s['id']=='targets'))
        self.assertTrue(any(b.get('kind')=='trajectory' for s in report['sections'] for b in s['blocks']))
        narrow = asyncio.run(make_report(self.state, {'year':2025,'scope':'AR-BUE'}, 'Test'))
        self.assertFalse(any(b.get('kind')=='trajectory' for s in narrow['sections'] for b in s['blocks']))

    def test_objectives_follow_selected_report_sections(self):
        self.login(); self.assertEqual(self.save().status_code, 200)
        response = self.client.post('/api/reports', headers=self.headers,
            json={'year':2025,'scope':'ALL','sections':['energy','gov']})
        self.assertEqual(response.status_code,200,response.text)
        report = response.json()
        self.assertEqual([s['id'] for s in report['sections']],['env','gov'])
        charts = [b for s in report['sections'] for b in s['blocks'] if b.get('kind')=='trajectory']
        self.assertTrue(any('energy' in b['id'] for b in charts))
        self.assertTrue(any('gov' in b['id'] for b in charts))
        self.assertFalse(any('climate' in b['id'] for b in charts))
        for chart in charts:
            self.assertIn(2026, chart['labels'])
            self.assertGreaterEqual(chart['labels'][-1],2027)
            self.assertEqual(len(chart['datasets']),2)
        self.state['targets'] = []
        self.assertEqual(self.save(1).status_code,200)
        empty = self.client.post('/api/reports',headers=self.headers,
            json={'year':2025,'scope':'ALL','sections':['energy']}).json()
        self.assertFalse(any(b.get('kind')=='trajectory' for s in empty['sections'] for b in s['blocks']))

    def test_carbon_integration_connect_map_sync_disconnect(self):
        self.login(); self.assertEqual(self.save().status_code, 200)
        loc_id = next(l['id'] for l in self.state['masterData']['locations'] if l.get('operable'))
        fake_sites = [{'id': 'S1', 'name': 'Planta Test', 'country': 'Argentina', 'cc': 'AR'}]
        fake_periods = ['2024-01', '2024-06', '2025-01']
        fake_summary = {'scopes': {'1': 1000.0, '2': 2000.0, '3': 3000.0}, 'scope2LocationKg': 2500.0}

        async def fake_carbon_get(path, token, params=None):
            self.assertEqual(token, 'ivzc_test-token')
            return {'/api/link/sites': fake_sites, '/api/link/periods': fake_periods, '/api/summary': fake_summary}[path]

        with patch('backend.carbon_link.carbon_get', fake_carbon_get):
            r = self.client.post('/api/integrations/carbon/connect', headers=self.headers, json={'token': 'ivzc_test-token'})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['sites'], fake_sites)
            self.assertTrue(self.client.get('/api/integrations/carbon').json()['connected'])

            self.assertEqual(self.client.put('/api/integrations/carbon/mapping', headers=self.headers,
                json={'siteMap': {'S1': 'not-a-real-location'}}).status_code, 422)
            r = self.client.put('/api/integrations/carbon/mapping', headers=self.headers, json={'siteMap': {'S1': loc_id}})
            self.assertEqual(r.status_code, 200, r.text)

            r = self.client.post('/api/integrations/carbon/sync', headers=self.headers, json={})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['lastCount'], 8)  # 2 years x 4 scope rows

            state = self.client.get('/api/state').json()['state']
            carbon_rows = [m for m in state['measures'] if m.get('measure') == 'CO2E' and m.get('src') == 'IVZ Carbon'
                           and m['loc'] == loc_id and m['y'] in (2024, 2025)]
            self.assertEqual(len(carbon_rows), 8)
            self.assertTrue(all(m['loc'] == loc_id for m in carbon_rows))
            s1_2024 = next(m for m in carbon_rows if m['y'] == 2024 and m['dims']['ghgScope'] == 'Scope 1')
            self.assertAlmostEqual(s1_2024['value'], 1.0)
            s2m = next(m for m in carbon_rows if m['dims'].get('allocationMethod') == 'Market-based')
            s2l = next(m for m in carbon_rows if m['dims'].get('allocationMethod') == 'Location-based')
            self.assertAlmostEqual(s2m['value'], 2.0)
            self.assertAlmostEqual(s2l['value'], 2.5)
            # Any pre-existing manual CO2E rows for that location/year must have been replaced, not duplicated.
            self.assertFalse(any(m.get('measure') == 'CO2E' and m['loc'] == loc_id and m['y'] in (2024, 2025)
                                  and m.get('src') != 'IVZ Carbon' for m in state['measures']))

        r = self.client.delete('/api/integrations/carbon', headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.client.get('/api/integrations/carbon').json()['connected'])

    def test_scope2_does_not_double_count(self):
        validate_state(self.state)
        for method, mid in [('Market-based', 'ENV-GHG-S2M'), ('Location-based', 'ENV-GHG-S2L')]:
            total = compute(self.state, 'ENV-GHG-TOT', 2026, s2=method)
            parts = sum(compute(self.state, x, 2026, s2=method) for x in ['ENV-GHG-S1', mid, 'ENV-GHG-S3'])
            self.assertAlmostEqual(total, parts, places=5)

    def test_mixed_periods_keep_each_location(self):
        self.state['actuals'] = [
            {'metricId':'SOC-INJ', 'loc':'AR-BUE', 'y':2026, 'periodType':'M', 'm':1, 'value':2},
            {'metricId':'SOC-INJ', 'loc':'AR-BUE', 'y':2026, 'periodType':'Y', 'value':100},
            {'metricId':'SOC-INJ', 'loc':'UY-MVD', 'y':2026, 'periodType':'Y', 'value':3}]
        self.assertEqual(compute(self.state, 'SOC-INJ', 2026), 5)

    def test_all_demo_indicators_match_frontend(self):
        for expected in json.loads(Path('data/expected.json').read_text()):
            with self.subTest(metric=expected['id']):
                actual = compute(self.state, expected['id'], 2026)
                if expected['value'] is None:
                    self.assertIsNone(actual)
                else:
                    self.assertAlmostEqual(actual, expected['value'], places=5)


if __name__ == '__main__':
    unittest.main()
