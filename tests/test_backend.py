import json
import os
import tempfile
import unittest
from pathlib import Path
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
                s.execute('INSERT INTO accounts VALUES (?, ?, ?, ?)', (user, user, user, hash_password('demopassword123')))
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

    def test_objectives_follow_selected_report_sections(self):
        self.login(); self.assertEqual(self.save().status_code, 200)
        response = self.client.post('/api/reports', headers=self.headers,
            json={'year':2025,'scope':'ALL','sections':['energy','gov']})
        self.assertEqual(response.status_code,200,response.text)
        report = response.json()
        self.assertEqual([s['id'] for s in report['sections']],['env','gov'])
        charts = [b for s in report['sections'] for b in s['blocks'] if b.get('kind')=='objectives']
        self.assertTrue(any('energy' in b['id'] for b in charts))
        self.assertTrue(any('gov' in b['id'] for b in charts))
        self.assertFalse(any('climate' in b['id'] for b in charts))
        for chart in charts:
            self.assertLessEqual(len(chart['labels']),4)
            self.assertEqual(len(chart['datasets']),2)
        self.state['targets'] = []
        self.assertEqual(self.save(1).status_code,200)
        empty = self.client.post('/api/reports',headers=self.headers,
            json={'year':2025,'scope':'ALL','sections':['energy']}).json()
        self.assertFalse(any(b.get('kind')=='objectives' for s in empty['sections'] for b in s['blocks']))

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
