import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import app
from backend.storage import SYSTEM, db
from backend.security import hash_password
from backend.metrics import compute, validate_state


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['SQLITE_PATH'] = str(Path(self.tmp.name) / 'test.sqlite')
        os.environ.pop('DATABASE_URL', None)
        os.environ.pop('GEMINI_API_KEY', None)
        self.use_database()
        self.client = TestClient(app).__enter__()
        self.headers = {'X-IVZ-Request': '1'}
        with db(SYSTEM) as s:
            for user in ['one', 'two']:
                s.execute('INSERT INTO accounts (id,username,company,password) VALUES (?, ?, ?, ?)', (user, user, user, hash_password('demopassword123')))
                s.execute("INSERT INTO users (id,account,username,password,role,created) VALUES (?, ?, ?, ?, 'admin', '')", (user, user, user, hash_password('demopassword123')))
        self.state = json.loads(Path('data/seed.json').read_text(encoding='utf8'))

    def use_database(self):
        """SQLite here; tests/test_isolation.py runs these same tests on PostgreSQL with row-level security."""

    def tearDown(self):
        self.client.__exit__(None, None, None)
        os.environ.pop('DATABASE_URL', None)
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

    def test_report_language_and_imported_target_names(self):
        import asyncio
        from backend.reports import make_report
        for target in self.state['targets']:
            target['name'] = 'Objetivo importado · ' + target['name']
        spanish = asyncio.run(make_report(self.state, {'year':2025,'scope':'ALL'}, 'Test'))
        english = asyncio.run(make_report(self.state, {'year':2025,'scope':'ALL','lang':'en'}, 'Test'))
        self.assertEqual(spanish['meta']['lang'], 'es')
        self.assertEqual(english['meta']['lang'], 'en')
        self.assertEqual(spanish['sections'][0]['title'], '1. Resumen ejecutivo')
        self.assertEqual(english['sections'][0]['title'], '1. Executive Summary')
        self.assertTrue(english['sections'][0]['blocks'][0]['text'].startswith('In 2025'))
        for report in (spanish, english):
            text = ' '.join(b.get('text','') + b.get('cap','') for s in report['sections'] for b in s['blocks'])
            self.assertNotIn('Objetivo importado', text)

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

    def test_admin_switches_sections_and_integrations_per_account(self):
        with db(SYSTEM) as s:
            s.execute("UPDATE accounts SET role='admin' WHERE id='two'")
        self.login(); self.assertEqual(self.save().status_code, 200)
        info = self.client.get('/api/state/catalogue').json()['features']
        self.assertTrue(all(info['sections'].values()) and all(info['integrations'].values()))
        self.assertEqual(self.client.put('/api/admin/accounts/one/settings', headers=self.headers, json={'sections': {'mapa': False}}).status_code, 403)
        self.login('two')
        for bad in ({'sections': {'inicio': False}}, {'integrations': {'INT-NOPE': True}}, {'sections': {'mapa': 'si'}}):
            self.assertEqual(self.client.put('/api/admin/accounts/one/settings', headers=self.headers, json=bad).status_code, 422)
        r = self.client.put('/api/admin/accounts/one/settings', headers=self.headers,
                            json={'sections': {'mapa': False, 'reportes': False}, 'integrations': {'INT-IVZC': False}})
        self.assertEqual(r.status_code, 200, r.text)
        cfg = self.client.get('/api/admin/accounts/one/settings').json()
        self.assertEqual({x['key'] for x in cfg['sections'] if not x['on']}, {'mapa', 'reportes'})
        self.assertEqual(cfg['carbon'], {'connected': False})
        self.assertEqual(self.client.post('/api/admin/accounts/one/carbon', headers=self.headers, json={'token': 'ivzc_x'}).status_code, 403)
        # The client sees the change and the server enforces it.
        self.login()
        info = self.client.get('/api/me').json()['features']
        self.assertEqual((info['sections']['mapa'], info['sections']['desempeno'], info['integrations']['INT-IVZC']), (False, True, False))
        self.assertEqual(self.client.get('/api/reports').status_code, 403)
        self.assertEqual(self.client.get('/api/integrations/carbon').status_code, 403)
        # Back on, the administrator connects IVZ Carbon for the client and disconnects it.
        self.login('two')
        self.client.put('/api/admin/accounts/one/settings', headers=self.headers, json={'integrations': {'INT-IVZC': True}})

        async def fake_carbon_get(path, token, params=None):
            return {'/api/link/sites': [{'id': 'S1', 'name': 'Planta', 'country': 'Argentina', 'cc': 'AR'}]}[path]
        with patch('backend.carbon_link.carbon_get', fake_carbon_get):
            r = self.client.post('/api/admin/accounts/one/carbon', headers=self.headers, json={'token': 'ivzc_test'})
        self.assertEqual(r.json(), {'connected': True, 'mapped': 0, 'lastSync': None, 'lastCount': None})
        self.assertEqual(self.client.post('/api/admin/accounts/one/carbon/sync', headers=self.headers).status_code, 422)  # nothing mapped yet
        self.login()
        with patch('backend.carbon_link.carbon_get', fake_carbon_get):
            self.assertTrue(self.client.get('/api/integrations/carbon').json()['connected'])
        self.login('two')
        self.assertEqual(self.client.delete('/api/admin/accounts/one/carbon', headers=self.headers).json(), {'connected': False})
        # The administrator renames the client's company; the client sees it under its user.
        self.assertEqual(self.client.put('/api/admin/accounts/one/company', headers=self.headers, json={'company': '  '}).status_code, 422)
        self.assertEqual(self.client.put('/api/admin/accounts/one/company', headers=self.headers, json={'company': 'IVZ Sustainability Hub'}).status_code, 200)
        self.login()
        self.assertEqual(self.client.get('/api/me').json()['company'], 'IVZ Sustainability Hub')

    def diff(self, before, after):
        """What the screen sends: the rest of the state if changed, changed/removed rows, the order only if it moved."""
        body = {'revision': before['revision'], 'changes': {}, 'order': {}}
        rest = lambda st: {k: v for k, v in st.items() if k not in ('measures', 'actuals')}
        if rest(before['state']) != rest(after):
            body['catalogue'] = rest(after)
        for kind in ('measures', 'actuals'):
            old = {x['id']: x for x in before['state'][kind]}
            ids = [x['id'] for x in after[kind]]
            keep = set(ids)
            upsert = [x for x in after[kind] if old.get(x['id']) != x]
            delete = [k for k in old if k not in keep]
            if upsert or delete:
                body['changes'][kind] = {'upsert': upsert, 'delete': delete}
            if ids != [k for k in old if k in keep] + [k for k in ids if k not in old]:
                body['order'][kind] = ids
        return body

    def paged(self):
        cat = self.client.get('/api/state/catalogue').json()
        state = dict(cat['state'])
        for kind in ('measures', 'actuals'):
            state[kind] = []
            for offset in range(0, cat['counts'][kind], 3000):
                page = self.client.get(f'/api/state/rows?kind={kind}&offset={offset}&limit=3000').json()
                self.assertEqual(page['revision'], cat['revision'])
                state[kind] += page['items']
        return {'revision': cat['revision'], 'state': state}

    def current(self):
        data = self.client.get('/api/state').json()
        return {'revision': data['revision'], 'state': data['state']}

    def test_saving_changes_matches_saving_everything(self):
        self.login()
        self.assertEqual(self.save().status_code, 200)
        current = self.current()
        self.assertEqual(current['state'], self.state)
        self.assertEqual(self.paged(), current)

        def edit(s): s['measures'][5]['value'] = 123.0; s['actuals'][7]['value'] = 0
        def delete(s): del s['measures'][10:30]; s['actuals'].pop(3)
        def add(s):
            s['measures'].append(dict(s['measures'][0], id='MSR-TEST-1', value=7.5))
            s['actuals'].append(dict(s['actuals'][0], id='ACT-TEST-1', value=2))
        def rest(s): s['targets'] = s['targets'][:3]; s['masterData']['locations'][0]['name'] = 'Planta renombrada'
        def reorder(s): s['measures'].reverse()
        def insert_middle(s): s['actuals'].insert(4, dict(s['actuals'][1], id='ACT-TEST-2', value=9))
        for step in (edit, delete, add, rest, reorder, insert_middle):
            with self.subTest(step=step.__name__):
                after = json.loads(json.dumps(current['state']))
                step(after)
                result = self.client.post('/api/state/changes', headers=self.headers, json=self.diff(current, after))
                self.assertEqual(result.status_code, 200, result.text)
                current = self.current()
                self.assertEqual(current['state'], after)
                self.assertEqual(self.paged(), current)
        self.assertEqual(compute(current['state'], 'ENV-GHG-TOT', 2026), compute(after, 'ENV-GHG-TOT', 2026))

    def test_changes_are_atomic_and_checked(self):
        self.login()
        self.assertEqual(self.save().status_code, 200)
        current = self.current()
        bad = json.loads(json.dumps(current['state']))
        bad['measures'][0]['loc'] = 'NO-EXISTE'
        self.assertEqual(self.client.post('/api/state/changes', headers=self.headers, json=self.diff(current, bad)).status_code, 422)
        stale = self.diff(current, dict(current['state'], targets=[]))
        stale['revision'] -= 1
        self.assertEqual(self.client.post('/api/state/changes', headers=self.headers, json=stale).status_code, 409)
        self.assertEqual(self.client.post('/api/state/changes', headers=self.headers,
                                          json={'revision': current['revision'], 'changes': {'measures': {'upsert': [{'value': 1}]}}}).status_code, 422)
        self.assertEqual(self.current(), current)
        # A large change arrives in staged parts; a missing part rejects the whole save.
        big = json.loads(json.dumps(current['state']))
        for row in big['measures']:
            row['value'] += 1
        body = self.diff(current, big)
        upsert = body['changes']['measures'].pop('upsert')
        chunks = [upsert[i:i + 4000] for i in range(0, len(upsert), 4000)]
        for batch, parts in (('batch-0001', len(chunks) + 1), ('batch-0002', len(chunks))):
            for i, chunk in enumerate(chunks):
                r = self.client.post('/api/state/upload', headers=self.headers, json={'batch': batch, 'part': i, 'changes': {'measures': {'upsert': chunk}}})
                self.assertEqual(r.status_code, 200, r.text)
            body['changes']['measures']['upsert'] = []
            body.update(batch=batch, parts=parts)
            status = self.client.post('/api/state/changes', headers=self.headers, json=body).status_code
            self.assertEqual(status, 409 if batch == 'batch-0001' else 200)
            if batch == 'batch-0001':
                self.assertEqual(self.current(), current)
        self.assertEqual(self.current()['state'], big)
        self.login('two')
        self.assertIsNone(self.client.get('/api/state/catalogue').json()['state'])
        self.assertEqual(self.client.get('/api/state/rows?kind=measures').status_code, 404)

    def test_single_body_states_are_moved_to_rows_once(self):
        with db(SYSTEM) as s:  # the pre-split format, as production has it; one row without an id
            legacy = json.loads(json.dumps(self.state))
            del legacy['actuals'][0]['id']
            s.execute('INSERT INTO states VALUES (?, 3, ?)', ('one', json.dumps(legacy)))
        self.login()
        migrated = self.current()
        self.assertEqual(migrated['revision'], 3)
        self.assertTrue(migrated['state']['actuals'][0]['id'])
        del migrated['state']['actuals'][0]['id']
        self.assertEqual(migrated['state'], legacy)
        with db(SYSTEM) as s:
            body = json.loads(s.execute("SELECT body FROM states WHERE account='one'").fetchone()['body'])
            backups = s.execute("SELECT body FROM state_backups WHERE account='one'").fetchall()
        self.assertNotIn('measures', body)
        self.assertEqual([json.loads(b['body']) for b in backups], [legacy])

    def test_closed_year_is_locked_until_an_admin_reopens_it(self):
        self.login()
        self.assertEqual(self.save().status_code, 200)
        current = self.current()
        r = self.client.post('/api/closures', headers=self.headers, json={'year': 2025})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['closedBy'], 'one')
        self.assertEqual(self.client.post('/api/closures', headers=self.headers, json={'year': 2025}).status_code, 409)
        self.assertEqual([c['year'] for c in self.client.get('/api/closures').json()], [2025])
        i25 = next(i for i, m in enumerate(current['state']['measures']) if m['y'] == 2025)
        i26 = next(i for i, m in enumerate(current['state']['measures']) if m['y'] == 2026)
        a25 = next(i for i, m in enumerate(current['state']['actuals']) if m['y'] == 2025)

        def attempt(change):
            after = json.loads(json.dumps(current['state']))
            change(after)
            return self.client.post('/api/state/changes', headers=self.headers, json=self.diff(current, after))
        def edit(s): s['measures'][i25]['value'] += 1
        def delete(s): s['actuals'].pop(a25)
        def add(s): s['measures'].append(dict(s['measures'][i25], id='MSR-CLOSED'))
        def move(s): s['measures'][i26]['y'] = 2025
        for change in (edit, delete, add, move):
            with self.subTest(change=change.__name__):
                r = attempt(change)
                self.assertEqual(r.status_code, 423, r.text)
                self.assertIn('2025', r.json()['detail'])
        full = json.loads(json.dumps(current['state']))
        full['measures'][i25]['value'] += 1
        self.assertEqual(self.client.put('/api/state', headers=self.headers, json={'revision': current['revision'], 'state': full}).status_code, 423)
        self.assertEqual(self.current(), current)
        def open_year(s): s['measures'][i26]['value'] += 1
        self.assertEqual(attempt(open_year).status_code, 200)
        current = self.current()
        # Reopening: only an administrator working inside the account, with a reason.
        self.assertEqual(self.client.post('/api/closures/2025/reopen', headers=self.headers, json={'reason': 'Corrección'}).status_code, 403)
        with db(SYSTEM) as s:
            s.execute("UPDATE accounts SET role='admin' WHERE id='two'")
        self.login('two')
        self.assertEqual(self.client.post('/api/admin/accounts/one/impersonate', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post('/api/closures/2025/reopen', headers=self.headers, json={'reason': ''}).status_code, 422)
        r = self.client.post('/api/closures/2025/reopen', headers=self.headers, json={'reason': 'Dato de planta corregido'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get('/api/closures').json(), [])
        self.assertTrue(any('Motivo: Dato de planta corregido' in e['action'] for e in self.client.get('/api/events').json()))
        self.assertEqual(attempt(edit).status_code, 200)

    def test_carbon_sync_leaves_closed_years_alone(self):
        self.login(); self.assertEqual(self.save().status_code, 200)
        loc_id = next(l['id'] for l in self.state['masterData']['locations'] if l.get('operable'))
        self.assertEqual(self.client.post('/api/closures', headers=self.headers, json={'year': 2024}).status_code, 200)
        before = [m for m in self.current()['state']['measures'] if m.get('measure') == 'CO2E' and m['loc'] == loc_id and m['y'] == 2024]

        async def fake_carbon_get(path, token, params=None):
            return {'/api/link/sites': [{'id': 'S1', 'name': 'Planta', 'country': 'Argentina', 'cc': 'AR'}],
                    '/api/link/periods': ['2024-01', '2025-01'],
                    '/api/summary': {'scopes': {'1': 1000.0, '2': 2000.0, '3': 3000.0}, 'scope2LocationKg': 2500.0}}[path]
        with patch('backend.carbon_link.carbon_get', fake_carbon_get):
            self.client.post('/api/integrations/carbon/connect', headers=self.headers, json={'token': 'ivzc_x'})
            self.client.put('/api/integrations/carbon/mapping', headers=self.headers, json={'siteMap': {'S1': loc_id}})
            r = self.client.post('/api/integrations/carbon/sync', headers=self.headers, json={})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['lastCount'], 4)  # 2025 only
        measures = self.current()['state']['measures']
        self.assertEqual([m for m in measures if m.get('measure') == 'CO2E' and m['loc'] == loc_id and m['y'] == 2024], before)
        self.assertEqual(sorted(m['id'] for m in measures if m['id'].startswith('CARBON-S1-')), ['CARBON-S1-2025-1', 'CARBON-S1-2025-2', 'CARBON-S1-2025-2L', 'CARBON-S1-2025-3'])

    def login_as(self, username, password):
        self.client.cookies.clear()
        return self.client.post('/api/login', headers=self.headers, json={'username': username, 'password': password})

    def test_a_company_has_several_users_with_roles(self):
        self.login(); self.assertEqual(self.save().status_code, 200)
        self.assertEqual(self.client.get('/api/me').json()['access'], 'admin')
        made = {}
        for name, role in (('ana', 'editor'), ('luis', 'viewer')):
            r = self.client.post('/api/users', headers=self.headers, json={'username': ' ' + name.upper(), 'role': role})
            self.assertEqual(r.status_code, 200, r.text)
            made[name] = r.json()
            self.assertEqual((made[name]['username'], made[name]['role']), (name, role))
        self.assertEqual(self.client.post('/api/users', headers=self.headers, json={'username': 'ana', 'role': 'viewer'}).status_code, 409)
        self.assertEqual(self.client.post('/api/users', headers=self.headers, json={'username': 'two', 'role': 'viewer'}).status_code, 409)  # another company's
        self.assertEqual([u['username'] for u in self.client.get('/api/users').json()], ['one', 'ana', 'luis'])
        # Both see the company's data; the editor saves, the viewer only reads.
        self.assertEqual(self.login_as('ana', made['ana']['password']).status_code, 200)
        me = self.client.get('/api/me').json()
        self.assertEqual((me['username'], me['company'], me['access']), ('ana', 'one', 'editor'))
        self.assertEqual(self.client.get('/api/state').json()['state']['measures'], self.state['measures'])
        self.assertEqual(self.save(1).status_code, 200)
        self.assertEqual(self.client.get('/api/users').status_code, 403)
        self.assertEqual(self.client.post('/api/users', headers=self.headers, json={'username': 'x', 'role': 'admin'}).status_code, 403)
        self.assertEqual(self.login_as('luis', made['luis']['password']).status_code, 200)
        self.assertEqual(self.client.get('/api/state').json()['revision'], 2)
        self.assertEqual(self.client.get('/api/state/catalogue').json()['access'], 'viewer')
        for method, path, body in (('put', '/api/state', {'revision': 2, 'state': self.state}), ('post', '/api/reports', {'year': 2026}),
                                   ('post', '/api/closures', {'year': 2024}), ('post', '/api/state/changes', {'revision': 2})):
            with self.subTest(path=path):
                r = getattr(self.client, method)(path, headers=self.headers, json=body)
                self.assertEqual((r.status_code, r.json()['detail']), (403, 'Tu usuario es de solo lectura.'))
        self.assertEqual(self.client.post('/api/logout', headers=self.headers).status_code, 200)
        # Who did what is recorded per user.
        self.login()
        self.assertIn(('Datos guardados', 'ana'), [(e['action'], e['actor']) for e in self.client.get('/api/events').json()])

    def test_a_company_keeps_an_active_admin_and_switched_off_users_are_out(self):
        self.login()
        ana = self.client.post('/api/users', headers=self.headers, json={'username': 'ana', 'role': 'editor'}).json()
        for change in ({'role': 'editor'}, {'active': False}):
            r = self.client.put('/api/users/one', headers=self.headers, json=change)
            self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self.client.put('/api/users/' + ana['id'], headers=self.headers, json={'role': 'admin'}).status_code, 200)
        self.assertEqual(self.client.put('/api/users/one', headers=self.headers, json={'role': 'viewer'}).json()['role'], 'viewer')
        self.assertEqual(self.client.put('/api/users/' + ana['id'], headers=self.headers, json={'role': 'editor'}).status_code, 403)  # now a viewer
        self.assertEqual(self.login_as('ana', ana['password']).status_code, 200)
        self.assertEqual(self.client.put('/api/users/' + ana['id'], headers=self.headers, json={'active': False}).status_code, 409)
        # Switching a user off ends its sessions.
        other = self.client.post('/api/users', headers=self.headers, json={'username': 'luis', 'role': 'editor'}).json()
        luis = TestClient(app); luis.post('/api/login', headers=self.headers, json={'username': 'luis', 'password': other['password']})
        self.assertEqual(luis.get('/api/me').status_code, 200)
        self.assertEqual(self.client.put('/api/users/' + other['id'], headers=self.headers, json={'active': False}).status_code, 200)
        self.assertEqual(luis.get('/api/me').status_code, 401)
        self.assertEqual(self.login_as('luis', other['password']).status_code, 403)
        self.login_as('ana', ana['password'])
        self.client.put('/api/users/' + other['id'], headers=self.headers, json={'active': True})
        self.assertEqual(self.login_as('luis', other['password']).status_code, 200)
        # Only Invenzis gives new passwords, never a company's admin.
        self.login_as('ana', ana['password'])
        self.assertEqual(self.client.post('/api/users/' + other['id'] + '/reset-password', headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post('/api/admin/accounts/one/users/' + other['id'] + '/reset-password', headers=self.headers).status_code, 403)
        self.assertEqual(self.login_as('luis', other['password']).status_code, 200)
        # A company never reaches the users of another one.
        self.login('two')
        self.assertEqual(self.client.put('/api/users/' + other['id'], headers=self.headers, json={'active': False}).status_code, 404)
        self.assertEqual([u['username'] for u in self.client.get('/api/users').json()], ['two'])

    def test_invenzis_admin_manages_the_users_of_a_client(self):
        with db(SYSTEM) as s:
            s.execute("UPDATE accounts SET role='admin' WHERE id='two'")
        self.login()
        self.assertEqual(self.client.get('/api/admin/accounts/one/users').status_code, 403)
        self.login('two')
        r = self.client.post('/api/admin/accounts/one/users', headers=self.headers, json={'username': 'ana', 'role': 'viewer'})
        self.assertEqual(r.status_code, 200, r.text)
        ana = r.json()
        self.assertEqual(self.client.put('/api/admin/accounts/one/users/' + ana['id'], headers=self.headers, json={'role': 'editor'}).json()['role'], 'editor')
        self.assertEqual(self.client.put('/api/admin/accounts/one/users/one', headers=self.headers, json={'active': False}).status_code, 409)
        self.assertEqual(self.client.put('/api/admin/accounts/two/users/' + ana['id'], headers=self.headers, json={'active': False}).status_code, 404)
        fresh = self.client.post('/api/admin/accounts/one/users/' + ana['id'] + '/reset-password', headers=self.headers).json()['password']
        listed = {r['id']: r for r in self.client.get('/api/admin/accounts').json()}
        self.assertEqual(listed['one']['users'], 2)
        self.assertEqual([u['username'] for u in self.client.get('/api/admin/accounts/one/users').json()], ['one', 'ana'])
        # Working inside the account, the administrator is its admin and is named as such.
        self.assertEqual(self.client.post('/api/admin/accounts/one/impersonate', headers=self.headers).status_code, 200)
        me = self.client.get('/api/me').json()
        self.assertEqual((me['username'], me['access'], me['impersonating']), ('Administrador de Invenzis', 'admin', True))
        self.assertEqual(len(self.client.get('/api/users').json()), 2)
        self.assertEqual(self.login_as('ana', ana['password']).status_code, 401)
        self.assertEqual(self.login_as('ana', fresh).status_code, 200)
        self.assertEqual(self.client.get('/api/admin/accounts').status_code, 403)

    def test_accounts_from_before_users_keep_their_login_and_sessions(self):
        from backend.storage import initialize
        from backend.security import token_hash
        with db(SYSTEM) as s:
            s.execute("INSERT INTO accounts (id,username,company,password,created) VALUES ('old','legacy','Vieja',?,'2026-01-01')",
                      (hash_password('demopassword123'),))
            s.execute('INSERT INTO sessions (token,account,expires) VALUES (?,?,?)', (token_hash('open-session'), 'old', 9e9))
        initialize()
        initialize()
        with db(SYSTEM) as s:
            self.assertEqual([dict(r) for r in s.execute("SELECT id,account,username,role FROM users WHERE account='old'").fetchall()],
                             [{'id': 'old', 'account': 'old', 'username': 'legacy', 'role': 'admin'}])
        self.client.cookies.set('ivz_session', 'open-session')
        self.assertEqual(self.client.get('/api/me').json()['username'], 'legacy')
        self.assertEqual(self.login_as('legacy', 'demopassword123').status_code, 200)

    def test_the_container_image_has_every_file_the_app_serves(self):
        import re
        root = Path(__file__).resolve().parent.parent
        served = set(re.findall(r"FileResponse\(ROOT / '([^']+)'", (root / 'backend/app.py').read_text(encoding='utf8')))
        dockerfile = root / 'Dockerfile'
        if dockerfile.exists():  # in the repository: the image copies each of them
            copied = {name for line in dockerfile.read_text(encoding='utf8').splitlines() if line.startswith('COPY ')
                      for name in line.split()[1:-1]}
            self.assertEqual(served - copied, set())
        else:  # inside the image, where the CI runs the tests: they are there
            self.assertEqual({name for name in served if not (root / name).exists()}, set())

    def test_every_connection_names_its_account(self):
        with self.assertRaises(TypeError), db():
            pass
        for bad in ('', None, 7):
            with self.subTest(account=bad), self.assertRaises(ValueError), db(bad):
                pass

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
