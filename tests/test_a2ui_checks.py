import copy
import json
import tempfile
import unittest
from pathlib import Path
from journey.a2ui_checks import CATALOG, evaluate_a2ui, question_action


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

    def test_cycles_fail_and_unrequested_optional_fields_do_not_change_data_verdict(self):
        events = self.events()
        components = events[-1]['payload']['updateComponents']['components']
        components[1]['title'] = 'Optional title'
        self.assertTrue(all(item['passed'] for item in self.check(events)[0]))
        components[0]['children'].append('root')
        self.assertFalse(all(item['passed'] for item in self.check(events)[0]))

    def test_question_submission_uses_observed_ids_values_and_unguessed_answer_path(self):
        events = self.events()
        components = events[-1]['payload']['updateComponents']['components']
        components[1] = {'id': 'table', 'component': 'Question', 'title': '2+3?', 'type': 'single',
                         'answerPath': '/answers/math', 'options': [
                             {'label': '4', 'value': 'opaque-no'}, {'label': '5', 'value': 'opaque-yes'}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            def action(rows):
                path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                return question_action(path, 'task', ['4', '5'], '5')
            result = action(events)['action']
            self.assertEqual(result['surfaceId'], 'surface')
            self.assertEqual(result['sourceComponentId'], 'table')
            self.assertEqual(result['context'], {'math': 'opaque-yes'})
            for field, value in [('result', 'correct'), ('answerPath', '/unexpected/math'),
                                 ('options', [{'label': '4', 'value': 'same'}, {'label': '5', 'value': 'same'}])]:
                bad = copy.deepcopy(events)
                bad[-1]['payload']['updateComponents']['components'][1][field] = value
                self.assertIsNone(action(bad))
            update = copy.deepcopy(events[-1])
            update['task_id'] = 'next'
            update['payload']['updateComponents']['components'][1]['result'] = 'correct'
            expected = {'component': 'Question', 'fields': {'id': 'table', 'result': 'correct'}}
            for task_id, passed in [('next', True), ('unrelated', False)]:
                update['task_id'] = task_id
                action(events + [update])
                checks, _ = evaluate_a2ui(path, 'next', 'session', expected, prior_task_id='task')
                self.assertEqual(all(check['passed'] for check in checks), passed)

    def test_multiple_and_text_answers_use_typed_observed_components(self):
        for kind, labels, answer, selected in [('multiple', ['2', '3', '4'], ['2', '4'], ['id-2', 'id-4']),
                                               ('text', [], 'cats', 'cats')]:
            events = self.events()
            question = {'id': 'table', 'component': 'Question', 'title': 'question', 'type': kind,
                        'answerPath': '/answers/observed'}
            if labels:
                question['options'] = [{'label': label, 'value': 'id-'+label} for label in labels]
            events[-1]['payload']['updateComponents']['components'][1] = question
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'events.jsonl'
                path.write_text(''.join(json.dumps(row)+'\n' for row in events))
                actual = question_action(path, 'task', labels, answer, question_type=kind)
                self.assertEqual(actual['action']['context'], {'observed': selected})
                self.assertIsNone(question_action(path, 'task', ['2', '3', '4'], '2', question_type='single'))
                question['result'] = 'correct'
                path.write_text(''.join(json.dumps(row)+'\n' for row in events))
                self.assertIsNone(question_action(path, 'task', labels, answer, question_type=kind))

    def test_invalid_answer_definitions_fail_before_submission(self):
        from journey.client import Failure
        for kind, labels, answer in [('multiple', ['2', '4'], '2'), ('multiple', ['2', '4'], ['2', '2']),
                                     ('multiple', ['2', '4'], ['unknown']), ('text', ['cats'], 'cats'),
                                     ('text', [], []), ('text', [], '')]:
            with self.assertRaises(Failure):
                question_action(Path('does-not-exist'), 'task', labels, answer, question_type=kind)
