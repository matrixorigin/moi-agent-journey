import json
import tempfile
import unittest
from pathlib import Path

from journey.skill_checks import check_skill_call


class SkillChecksTests(unittest.TestCase):
    def evaluate(self, events):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            return check_skill_call(path, 'task', 'skill.xlsx', 'session')

    def event(self, kind, call_id, *, task='task', skill='skill.xlsx', success=None):
        return {'kind': kind, 'task_id': task, 'payload': {'tool_id': 'skill', 'call_id': call_id,
                'arguments': {'skill_name': skill}, 'success': success}}

    def test_matching_skill_requires_a_successful_result_for_the_same_task_and_call(self):
        events = [self.event('tool_call', 'a'), self.event('tool_result', 'a', success=True)]
        check, blocked = self.evaluate(events)
        self.assertTrue(check['passed'])
        self.assertIsNone(blocked)
        check, _ = self.evaluate([events[0], self.event('tool_result', 'other', success=True)])
        self.assertFalse(check['passed'])
        check, _ = self.evaluate([events[0], self.event('tool_result', 'a', task='other', success=True)])
        self.assertFalse(check['passed'])
        check, _ = self.evaluate([self.event('tool_call', 'a', skill='other'), events[1]])
        self.assertFalse(check['passed'])

    def test_missing_result_is_blocked_and_explicit_failure_is_not_success(self):
        check, blocked = self.evaluate([self.event('tool_call', 'a')])
        self.assertFalse(check['passed'])
        self.assertEqual(blocked, 'skill_result_unavailable')
        check, blocked = self.evaluate([self.event('tool_call', 'a'), self.event('tool_result', 'a', success=False)])
        self.assertFalse(check['passed'])
        self.assertIsNone(blocked)


if __name__ == '__main__':
    unittest.main()
