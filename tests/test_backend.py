import json
import os
import tempfile
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
        with db() as s:
            for user in ['one', 'two']:
                s.execute('INSERT INTO accounts (id,username,company,password) VALUES (?, ?, ?, ?)', (user, user, user, hash_password('demopassword123')))
        self.state = json.loads(Path('data/seed.json').read_text(encoding='utf8'))

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def login(self, user='one'):
        result = self.client.post('/api/login', headers=self.headers, json={'username': user, 'password': 'demopassword123'})
        self.assertEqual(result.status_code, 200)

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
