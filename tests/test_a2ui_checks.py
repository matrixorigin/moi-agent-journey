import copy
import json
import tempfile
import unittest
from pathlib import Path
from journey.a2ui_checks import CATALOG, evaluate_a2ui


class A2UICheckTests(unittest.TestCase):
    def check(self, rows, expected=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            return evaluate_a2ui(path, 'task', 'session', expected or {
                'component': 'Table', 'fields': {'rows': [{'item': 'A', 'count': 3}]}})

    def events(self):
        return [{'task_id': 'task', 'kind': 'a2ui', 'payload': {'version': 'v0.9',
                 'createSurface': {'surfaceId': 'surface', 'catalogId': CATALOG}}},
                {'task_id': 'task', 'kind': 'a2ui', 'payload': {'version': 'v0.9', 'updateComponents': {
                 'surfaceId': 'surface', 'components': [{'id': 'root', 'component': 'Column', 'children': ['table']},
                    {'id': 'table', 'component': 'Table', 'columns': ['item', 'count'], 'rows': [{'item': 'A', 'count': 3}]}]}}}]

    def test_task_bound_surface_uses_final_component_update(self):
        events = self.events()
        checks, delivered = self.check(events)
        self.assertTrue(delivered)
        self.assertTrue(all(item['passed'] for item in checks))
        update = copy.deepcopy(events[-1])
        update['payload']['updateComponents']['components'][1]['rows'][0]['count'] = 4
        checks, _ = self.check(events + [update])
        self.assertFalse(checks[-1]['passed'])
        update['task_id'] = 'different-task'
        self.assertTrue(all(item['passed'] for item in self.check(events + [update])[0]))

    def test_unreachable_or_wrong_surface_never_passes(self):
        for fault in ('root', 'surface', 'missing', 'raw_text'):
            events = self.events()
            if fault == 'root':
                events[-1]['payload']['updateComponents']['components'][0]['children'] = []
            elif fault == 'surface':
                events[-1]['payload']['updateComponents']['surfaceId'] = 'wrong'
            elif fault == 'missing':
                events.pop(0)
            else:
                for event in events:
                    event['kind'] = 'text_delta'
            checks, delivered = self.check(events)
            self.assertFalse(delivered)
            self.assertFalse(all(item['passed'] for item in checks))
