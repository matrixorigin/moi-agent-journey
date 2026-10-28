import csv
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from journey.catalog import fixture_path, load_case
from journey.client import Client, Failure
from journey.functional_case import FunctionalExecution, case_variables, check_distinct_attachment_ids, check_reply
from journey.outcomes import assess
from journey.report import finish_case
from journey.run_case import agent_stream
from test_journeys import qa_fixture


class FunctionalJourneyTests(unittest.TestCase):
    def make_case(self):
        return {'id': 'J2-03', 'title': 'fixture', 'workspace': 'journey-1', 'executor': 'api',
                'timeout_seconds': 30, 'assets': [], 'oracles': [], 'source': {'benchmark': 'fixture'},
                'random_tokens': ['code'], 'sessions': [{'id': 'context', 'delivery_kind': 'answer',
                    'turns': [{'phase': 'first', 'prompt': 'remember {code}', 'assertions': [
                        {'type': 'contains', 'value': 'done', 'rule': 'first answer'}]},
                        {'phase': 'second', 'prompt': 'recall', 'assertions': [
                            {'type': 'contains', 'value': 'done', 'rule': 'second answer'}]}]}]}

    def test_real_stream_adapter_preserves_one_session_and_each_task(self):
        with qa_fixture('success') as (base, state):
            execution = FunctionalExecution(base.client, self.make_case(), base.evidence, base.work, 'model')
            result = execution.run()
            sends = [body for _, path, body in state['methods'] if path == '/agents/a2a' and
                     body.get('method') == 'message/stream']
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(len(sends), 2)
            self.assertEqual(sends[0]['params']['message']['contextId'], sends[1]['params']['message']['contextId'])
            self.assertNotEqual(sends[0]['params']['message']['messageId'], sends[1]['params']['message']['messageId'])
            self.assertEqual([task['phase'] for task in result['tasks']], ['context:first', 'context:second'])
            self.assertTrue(result['capture_complete'])
            self.assertTrue(state['archived'])
            self.assertEqual(len(result['validation']['checks']), 4)
            task, agent, delivery, quality = assess(execution.case, result, result['status'])
            self.assertEqual((task['state'], agent['state'], delivery['state'], quality['state']),
                             ('completed', 'completed', 'delivered', 'checked'))

    def test_agent_failure_preserves_partial_trace_and_is_not_an_executor_error(self):
        with qa_fixture('agent_failure') as (base, state):
            execution = FunctionalExecution(base.client, self.make_case(), base.evidence, base.work, 'model')
            result = execution.run()
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['failures'][0]['reason'], 'agent_failed')
            self.assertEqual(len(result['tasks']), 2)
            self.assertTrue(result['capture_complete'])
            self.assertEqual(result['harness_errors'], [])
            self.assertTrue(state['archived'])
            _, _, _, quality = assess(execution.case, result, result['status'])
            self.assertEqual((quality['state'], quality['reason']), ('partial', 'unfinished_turns'))

    def test_admitted_runtime_stream_error_is_a_function_failure_with_trace(self):
        with qa_fixture('runtime_stream_error') as (base, state):
            case = self.make_case()
            execution = FunctionalExecution(base.client, case, base.evidence, base.work, 'model')
            result = execution.run()
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['harness_errors'], [])
            self.assertEqual(result['failures'][0]['reason'], 'agent_runtime_stream_error')
            self.assertEqual(result['failures'][0]['details']['data']['reason'], 'INTERNAL')
            self.assertEqual(result['tasks'][0]['trace_id'], 'trace-task-1')
            self.assertTrue(result['capture_complete'])
            self.assertTrue(state['archived'])
            sends = [body for _, path, body in state['methods'] if path == '/agents/a2a' and
                     body.get('method') == 'message/stream']
            self.assertEqual(len(sends), 1)
            with tempfile.TemporaryDirectory() as directory:
                shutil.copytree(base.evidence.root, Path(directory) / case['id'])
                with patch('journey.report.load_case', return_value=case):
                    report = finish_case(case['id'], directory)
            self.assertEqual(report['test_status'], 'completed')
            self.assertEqual(report['functional']['state'], 'fail')
            self.assertEqual(report['agent']['state'], 'abnormal')

    def test_only_an_admitted_matching_runtime_stream_error_is_reclassified(self):
        details = {'code': -32603, 'data': {'domain': 'moi-core.agent_runtime', 'reason': 'INTERNAL'}}
        def broken(error):
            raise error
            yield
        for pending, phase, payload, expected in [
            (None, 'session:turn', details, 'agent_runtime_stream_error'),
            ('conversation', 'session:turn', details, 'rpc_error'),
            (None, 'session:previous', details, 'rpc_error'),
            (None, 'session:turn', {'code': -32603, 'data': {'domain': 'other', 'reason': 'INTERNAL'}}, 'rpc_error'),
        ]:
            execution = SimpleNamespace(pending_conversation=pending,
                tasks=[{'conversation_id': 'conversation', 'phase': phase}])
            with self.subTest(expected=expected, pending=pending, phase=phase), self.assertRaises(Failure) as raised:
                list(agent_stream(broken(Failure('rpc_error', details=payload)), execution,
                                  'conversation', 'session:turn'))
            self.assertEqual(raised.exception.code, expected)

    def test_wrong_final_answer_fails_function_without_losing_evidence(self):
        with qa_fixture('success') as (base, state):
            case = self.make_case()
            case['sessions'][0]['turns'][1]['assertions'][0]['value'] = 'not in answer'
            execution = FunctionalExecution(base.client, case, base.evidence, base.work, 'model')
            result = execution.run()
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['failures'][0]['reason'], 'acceptance_checks_failed')
            self.assertEqual(result['harness_errors'], [])
            self.assertTrue(result['capture_complete'])
            self.assertTrue(state['archived'])
            task, agent, delivery, quality = assess(case, result, result['status'])
            self.assertEqual((task['state'], agent['state'], delivery['state']), ('completed', 'completed', 'delivered'))
            self.assertEqual(len(quality['failed_checks']), 1)

    def test_required_skill_is_checked_against_the_actual_task(self):
        with qa_fixture('success') as (base, _state):
            case = self.make_case()
            case['sessions'][0]['turns'] = [{'phase': 'read', 'prompt': 'read PDF',
                                              'skill_evidence': 'moi.agent.momo.skill.pdf'}]
            execution = FunctionalExecution(base.client, case, base.evidence, base.work, 'model')
            check = {'session': 'context', 'rule': '实际调用指定 Skill 并成功',
                     'passed': False, 'detail': 'skill_call_not_successful'}
            with patch('journey.functional_case.check_skill_call', return_value=(check, None)) as validate:
                result = execution.run()
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['validation']['checks'][-1], check)
            self.assertEqual(validate.call_args.args[1:], (result['tasks'][0]['task_id'],
                                                          'moi.agent.momo.skill.pdf', 'context'))

    def test_empty_answer_is_not_reported_as_a_delivery(self):
        with qa_fixture('success') as (base, _state):
            case = self.make_case()
            execution = FunctionalExecution(base.client, case, base.evidence, base.work, 'model')
            with patch('journey.functional_case.final_reply', return_value=''):
                result = execution.run()
            self.assertEqual(result['status'], 'failed')
            self.assertFalse(result['sessions'][0]['answer_delivered'])
            _, _, delivery, _ = assess(case, result, result['status'])
            self.assertEqual(delivery['state'], 'not_delivered')

    def test_quota_exhaustion_stays_blocked_even_if_the_agent_task_fails(self):
        with qa_fixture('search_quota') as (base, state):
            case = self.make_case()
            case['sessions'][0]['turns'] = [{'phase': 'query', 'prompt': 'search', 'search_evidence': True}]
            execution = FunctionalExecution(base.client, case, base.evidence, base.work, 'model')
            result = execution.run()
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(result['blocked'])
            self.assertIn('WEB_QUOTA_EXCEEDED', [item['reason'] for item in result['failures']])
            self.assertTrue(result['capture_complete'])
            self.assertTrue(state['archived'])

    def test_uploaded_fixture_is_attached_and_its_retention_is_recorded(self):
        with qa_fixture('success') as (base, state):
            case = self.make_case()
            case['fixtures'] = [{'path': 'image.png', 'sha256': 'test'}]
            case['sessions'][0]['turns'][0]['attachments'] = ['image.png']
            execution = FunctionalExecution(base.client, case, base.evidence, base.work, 'model')
            with patch('journey.functional_case.fixture_path', return_value=base.work / 'inputs' / 'a.txt'):
                result = execution.run()
            sends = [body for _, path, body in state['methods'] if path == '/agents/a2a' and
                     body.get('method') == 'message/stream']
            self.assertEqual(sends[0]['params']['message']['parts'][1]['file']['uri'], 'moi://files/input')
            self.assertEqual(result['retained_inputs'][0]['file_id'], 'input')
            self.assertEqual(result['retained_inputs'][0]['fixture'], 'image.png')

    def test_multiple_same_name_uploads_keep_identity_in_the_message(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate), qa_fixture('success') as (base, state):
                case = self.make_case()
                names = ['north/sales.csv', 'south/sales.csv']
                case['fixtures'] = [{'path': name, 'sha256': 'test'} for name in names]
                case['sessions'][0]['expected_checks'] = 2
                case['sessions'][0]['turns'] = [{'phase': 'read', 'prompt': 'read both',
                    'attachments': names, 'distinct_attachment_ids': True}]
                execution = FunctionalExecution(base.client, case, base.evidence, base.work, 'model')
                paths = [base.work / name for name in names]
                uploads = [{'file_id': 'north', 'original_name': 'sales.csv'},
                           {'file_id': 'north' if duplicate else 'south', 'original_name': 'sales.csv'}]
                with patch('journey.functional_case.fixture_path', side_effect=paths), \
                     patch.object(base.client, 'upload', side_effect=uploads):
                    result = execution.run()
                sends = [body for _, path, body in state['methods'] if path == '/agents/a2a' and
                         body.get('method') == 'message/stream']
                parts = sends[0]['params']['message']['parts'][1:]
                self.assertEqual([part['file']['name'] for part in parts], ['sales.csv', 'sales.csv'])
                self.assertEqual([part['metadata']['file_id'] for part in parts],
                                 ['north', 'north' if duplicate else 'south'])
                self.assertEqual(result['status'], 'failed' if duplicate else 'passed')
                self.assertEqual(result['harness_errors'], [])
                self.assertTrue(result['capture_complete'])
                self.assertEqual(result['validation']['checks'][0]['passed'], not duplicate)
                self.assertTrue(state['archived'])
                with tempfile.TemporaryDirectory() as directory:
                    shutil.copytree(base.evidence.root, Path(directory) / case['id'])
                    with patch('journey.report.load_case', return_value=case):
                        report = finish_case(case['id'], directory)
                self.assertEqual(report['test_status'], 'completed')
                self.assertEqual(report['functional']['state'], 'fail' if duplicate else 'pass')

    def test_distinct_attachment_check_rejects_invalid_test_configuration(self):
        for names, uploads in [([], []), (['same', 'same'], [{'file_id': 'a'}, {'file_id': 'b'}]),
                               (['one', 'two'], [{'file_id': 'a'}])]:
            with self.subTest(names=names), self.assertRaisesRegex(Failure, 'invalid_distinct_attachments'):
                check_distinct_attachment_ids('files', names, uploads)

    def test_invalid_upload_metadata_is_a_product_failure_without_sending_a_task(self):
        for upload in ({'original_name': 'sales.csv'}, {'file_id': 'id', 'original_name': ''}):
            with self.subTest(upload=upload), qa_fixture('success') as (base, state):
                case = self.make_case()
                case['fixtures'] = [{'path': 'sales.csv', 'sha256': 'test'}]
                case['sessions'][0]['turns'] = [{'phase': 'read', 'prompt': 'read', 'attachments': ['sales.csv']}]
                execution = FunctionalExecution(base.client, case, base.evidence, base.work, 'model')
                execution.collect_artifacts = lambda cid: []
                with patch('journey.functional_case.fixture_path', return_value=base.work / 'sales.csv'), \
                     patch.object(base.client, 'upload', return_value=upload):
                    result = execution.run()
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['harness_errors'], [])
                self.assertEqual(result['validation']['checks'][0]['detail'], 'invalid_upload_metadata')
                self.assertEqual(result['tasks'], [])
                self.assertTrue(state['archived'])
                self.assertEqual(json.loads((base.evidence.root / 'uploads.json').read_text()), [upload])
                with tempfile.TemporaryDirectory() as directory:
                    shutil.copytree(base.evidence.root, Path(directory) / case['id'])
                    with patch('journey.report.load_case', return_value=case):
                        report = finish_case(case['id'], directory)
                self.assertEqual(report['test_status'], 'completed')
                self.assertEqual(report['functional']['state'], 'fail')

    def test_upload_error_distinguishes_product_rejection_from_environment_and_transport(self):
        failures = [
            (Failure('http_error', 409), 'completed', 'fail'),
            (Failure('http_error', 500), 'completed', 'fail'),
            (Failure('http_error', 429), 'completed', 'blocked'),
            (Failure('http_error', 503), 'completed', 'blocked'),
            (Failure('upload_product_error', details={'product_code': 'BAD_FILE'}), 'completed', 'fail'),
            (Failure('http_error', 401), 'error', 'error'),
            (Failure('transport_error', details={'category': 'timeout'}), 'error', 'error'),
        ]
        for error, test_status, verdict in failures:
            with self.subTest(error=error.code, http_status=error.http_status), qa_fixture('success') as (base, state):
                case = self.make_case()
                case['fixtures'] = [{'path': 'sales.csv', 'sha256': 'test'}]
                case['sessions'][0]['turns'] = [{'phase': 'read', 'prompt': 'read', 'attachments': ['sales.csv']}]
                execution = FunctionalExecution(base.client, case, base.evidence, base.work, 'model')
                execution.collect_artifacts = lambda cid: []
                with patch('journey.functional_case.fixture_path', return_value=base.work / 'sales.csv'), \
                     patch.object(base.client, 'upload', side_effect=error):
                    result = execution.run()
                self.assertEqual(result['tasks'], [])
                self.assertTrue(state['archived'])
                errors = result['harness_errors'] if verdict == 'error' else result['failures']
                observed = next(item for item in errors if item['reason'] == error.code)
                self.assertEqual(observed['http_status'], error.http_status)
                self.assertEqual(observed['details'], error.details)
                with tempfile.TemporaryDirectory() as directory:
                    shutil.copytree(base.evidence.root, Path(directory) / case['id'])
                    with patch('journey.report.load_case', return_value=case):
                        report = finish_case(case['id'], directory)
                self.assertEqual(report['test_status'], test_status)
                self.assertEqual(report['functional']['state'], verdict)

    def test_upload_preserves_business_error_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.csv'
            path.write_text('data')
            client = Client('https://fixture.invalid', 'secret_key')
            with patch.object(client, 'request', return_value={'code': 'BAD_FILE'}), self.assertRaises(Failure) as raised:
                client.upload('/workspaces/ws', path)
            self.assertEqual(raised.exception.code, 'upload_product_error')
            self.assertEqual(raised.exception.details, {'product_code': 'BAD_FILE'})

    def test_multiple_file_cases_only_change_filenames(self):
        normal, same_name = load_case('J2-24'), load_case('J2-25')
        first, second = normal['sessions'][0]['turns'][0], same_name['sessions'][0]['turns'][0]
        self.assertEqual(first['prompt'], second['prompt'])
        self.assertEqual(first['assertions'], second['assertions'])
        normal_paths = [fixture_path(normal, fixture) for fixture in normal['fixtures']]
        same_paths = [fixture_path(same_name, fixture) for fixture in same_name['fixtures']]
        self.assertNotEqual(normal_paths[0].name, normal_paths[1].name)
        self.assertEqual(same_paths[0].name, same_paths[1].name)
        self.assertEqual([path.read_bytes() for path in normal_paths], [path.read_bytes() for path in same_paths])
        amounts = []
        for path in normal_paths:
            with path.open(newline='', encoding='utf-8') as source:
                amounts.extend(int(row['quantity']) * int(row['unit_price']) for row in csv.DictReader(source))
        self.assertEqual(str(sum(amounts)), first['assertions'][0]['value'])

    def test_qa_deployment_change_blocks_an_otherwise_passing_case(self):
        with qa_fixture('version_change') as (base, state):
            execution = FunctionalExecution(base.client, self.make_case(), base.evidence, base.work, 'model')
            result = execution.run()
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(result['blocked'])
            self.assertEqual(result['qa_version_start']['version'], 'v1')
            self.assertEqual(result['qa_version_end']['version'], 'v2')
            self.assertIn('qa_backend_changed_during_case', [item['reason'] for item in result['failures']])
            self.assertTrue(result['capture_complete'])
            self.assertTrue(state['archived'])

    def test_answer_rules_check_actual_reply_without_exact_prose_matching(self):
        turn = {'phase': 'recall', 'assertions': [
            {'type': 'contains', 'value': '{new}', 'rule': 'new code'},
            {'type': 'not_contains', 'value': '{old}', 'rule': 'old code absent'}]}
        values = {'new': 'NEW_123', 'old': 'OLD_456'}
        self.assertTrue(all(x['passed'] for x in check_reply('context', turn, '当前是 NEW_123。', values)))
        wrong = check_reply('context', turn, 'NEW_123，旧口令 OLD_456', values)
        self.assertFalse(wrong[-1]['passed'])
        with self.assertRaises(Failure):
            check_reply('context', {'phase': 'x', 'assertions': [{'type': 'unknown', 'value': 'x'}]}, 'x', {})

    def test_numeric_result_requires_the_only_numeric_value_to_match(self):
        turn = {'phase': 'total', 'assertions': [{'type': 'single_number', 'value': '41', 'rule': 'total'}]}
        self.assertTrue(check_reply('file', turn, '总金额是41.00元。', {})[-1]['passed'])
        self.assertFalse(check_reply('file', turn, '总金额是141元。', {})[-1]['passed'])
        self.assertFalse(check_reply('file', turn, '41是旧值，正确结果是42。', {})[-1]['passed'])

    def test_each_case_run_allocates_distinct_context_tokens(self):
        case = load_case('J2-03')
        first, second = case_variables(case), case_variables(case)
        self.assertNotEqual(first['old_code'], first['new_code'])
        self.assertNotEqual(first, second)

    def test_case_catalog_accepts_three_digit_ids_without_accepting_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_dir = root / 'cases' / 'journey-2' / 'J2-100'
            case_dir.mkdir(parents=True)
            (case_dir / 'case.json').write_text(json.dumps({'id': 'J2-100', 'workspace': 'journey-2'}))
            (root / 'suites.json').write_text(json.dumps({'case_timeout_seconds': 60, 'model': 'test',
                                                           'journeys': [{'workspace': 'journey-2'}]}))
            with patch('journey.catalog.ROOT', root):
                self.assertEqual(load_case('J2-100')['id'], 'J2-100')
                with self.assertRaisesRegex(ValueError, 'invalid_case_id'):
                    load_case('J2-100/../../other')

    def test_resource_change_spec_rejects_ambiguous_operations(self):
        from journey.client import Failure
        from journey.functional_case import resource_changes
        valid = {'resource_changes': {'skills': {'remove': ['skill.xlsx']}, 'tools': {'add': ['tool.search']}}}
        self.assertEqual(resource_changes(valid), valid['resource_changes'])
        for invalid in ({}, {'skills': {'add': ['same'], 'remove': ['same']}},
                        {'knowledge_bases': {'add': ['kb']}}, {'skills': {'remove': []}}):
            with self.subTest(invalid=invalid), self.assertRaises(Failure):
                resource_changes({'resource_changes': invalid})

    def test_committed_fixture_is_checksum_verified_and_cannot_escape_case_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / 'cases' / 'journey-2' / 'J2-02'
            directory.mkdir(parents=True)
            (directory / 'image.png').write_bytes(b'actual fixture')
            fixture = {'path': 'image.png', 'sha256': hashlib.sha256(b'actual fixture').hexdigest()}
            with patch('journey.catalog.ROOT', root):
                self.assertEqual(fixture_path({'id': 'J2-02'}, fixture), (directory / 'image.png').resolve())
                (directory / 'image.png').write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'fixture_checksum_mismatch'):
                    fixture_path({'id': 'J2-02'}, fixture)
                with self.assertRaisesRegex(ValueError, 'unsafe_fixture_path'):
                    fixture_path({'id': 'J2-02'}, {'path': '../other.png', 'sha256': 'x'})


if __name__ == '__main__':
    unittest.main()
