import json
import tempfile
import unittest
from pathlib import Path

from journey.search_checks import evaluate_search, url_identity


class SearchChecksTests(unittest.TestCase):
    def evaluate(self, output, reply, *, call_id='call1', result_id='call1', success=True):
        events = [
            {'task_id': 'task', 'kind': 'tool_call', 'payload': {'tool_id': 'moi_web_search', 'call_id': call_id}},
            {'task_id': 'task', 'kind': 'tool_result', 'payload': {
                'tool_id': 'moi_web_search', 'call_id': result_id, 'success': success, 'output': output}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.jsonl'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            return evaluate_search(path, 'task', reply, 'search')

    def test_answer_must_reference_a_url_returned_by_the_search(self):
        output = {'output': {'ok': True, 'results': [{'url': 'https://www.iana.org/domains/reserved'}]}}
        checks, blocked = self.evaluate(output, '来源：https://www.iana.org/domains/reserved。')
        self.assertTrue(all(check['passed'] for check in checks))
        self.assertIsNone(blocked)
        checks, blocked = self.evaluate(output, '来源：https://www.iana.org.evil/domains/reserved')
        self.assertFalse(checks[-1]['passed'])
        self.assertIsNone(blocked)
        checks, _ = self.evaluate(output, 'https://www.iana.org/domains/reserved', result_id='different')
        self.assertFalse(checks[1]['passed'])

    def test_quota_and_truncated_results_are_reported_as_blocked(self):
        checks, blocked = self.evaluate({'output': {'ok': False, 'error_code': 'WEB_QUOTA_EXCEEDED'}}, '无法搜索')
        self.assertEqual(blocked, 'WEB_QUOTA_EXCEEDED')
        self.assertFalse(checks[1]['passed'])
        checks, blocked = self.evaluate({'type': 'summary', 'truncated': True}, '无法验证')
        self.assertEqual(blocked, 'search_result_truncated')
        self.assertFalse(checks[2]['passed'])

    def test_citation_can_use_the_observed_final_url_of_an_opened_search_result(self):
        events = [
            {'task_id': 'task', 'kind': 'tool_call', 'payload': {'tool_id': 'moi_web_search', 'call_id': 's'}},
            {'task_id': 'task', 'kind': 'tool_result', 'payload': {'tool_id': 'moi_web_search', 'call_id': 's',
             'success': True, 'output': {'output': {'ok': True, 'results': [{'url': 'https://example.com/old'}]}}}},
            {'task_id': 'task', 'kind': 'tool_call', 'payload': {'tool_id': 'moi_web_open', 'call_id': 'o',
             'arguments': {'url': 'https://example.com/old'}}},
            {'task_id': 'task', 'kind': 'tool_result', 'payload': {'tool_id': 'moi_web_open', 'call_id': 'o',
             'success': True, 'output': {'output': {'ok': True, 'url': 'https://example.com/new'}}}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.jsonl'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            checks, blocked = evaluate_search(path, 'task', '来源：https://example.com/new', 'search')
        self.assertTrue(all(check['passed'] for check in checks))
        self.assertIsNone(blocked)

    def test_invalid_or_credential_bearing_urls_are_not_sources(self):
        self.assertIsNone(url_identity(None))
        self.assertIsNone(url_identity('https://user:password@example.com/path'))
        self.assertIsNone(url_identity('not-a-url'))
        self.assertEqual(url_identity('https://EXAMPLE.com/path/。'), ('https', 'example.com', None, '/path', ''))
        self.assertEqual(url_identity('https://example.com:443/path'), ('https', 'example.com', None, '/path', ''))

    def test_created_agent_must_use_the_tool_that_was_bound(self):
        events = [
            {'task_id': 'task', 'kind': 'tool_call', 'payload': {'tool_id': 'moi_bocha_web_search', 'call_id': 's'}},
            {'task_id': 'task', 'kind': 'tool_result', 'payload': {'tool_id': 'moi_bocha_web_search', 'call_id': 's',
             'success': True, 'output': {'output': {'ok': True, 'results': [{'url': 'https://example.com/'}]}}}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.jsonl'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            checks, blocked = evaluate_search(path, 'task', 'https://example.com/', 'search', expected_tool='moi_web_search')
        self.assertFalse(any(check['passed'] for check in checks))
        self.assertIsNone(blocked)


if __name__ == '__main__':
    unittest.main()
