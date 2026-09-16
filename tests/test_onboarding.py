import copy
import json
import unittest
from pathlib import Path
from backend.metrics import validate_state


class OnboardingTests(unittest.TestCase):
    def test_blank_and_first_root(self):
        state = json.loads(Path('data/empty-state.json').read_text(encoding='utf8'))
        self.assertTrue(all(not rows for rows in state['masterData'].values()))
        self.assertEqual(len(state['metrics']), 30)
        validate_state(state)
        state['masterData']['legalEntities'].append({'id':'EMPRESA', 'name':'Mi empresa'})
        state['masterData']['locations'].append({'id':'OFICINA', 'name':'Mi oficina', 'parent':None, 'entity':'EMPRESA', 'operable':True})
        validate_state(state)
        invalid = copy.deepcopy(state)
        invalid['masterData']['locations'][0]['parent'] = 'GRP'
        with self.assertRaises(ValueError):
            validate_state(invalid)
