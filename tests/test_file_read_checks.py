import copy
import json
import tempfile
import unittest
from pathlib import Path

from journey.file_read_checks import evaluate_file_rejection


class FileReadChecksTests(unittest.TestCase):
    def checks(self, events, name='broken.zip'):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            return evaluate_file_rejection(path, 'task', 'session',
                                           {'file_id': 'owned-file', 'original_name': name})

    def events(self, name='broken.zip', error='BadZipFile: File is not a zip file'):
        tool = 'mcp__moi-tools__bash'
        commands = [('download', 'moi-cli file download --file-id owned-file --output /tmp/' + name),
                    ('decode', "python3 -c \"import zipfile; zipfile.ZipFile('/tmp/" + name + "')\"")]
        events = []
        for call_id, command in commands:
            events.extend([
                {'task_id': 'task', 'kind': 'tool_call', 'payload': {
                    'tool_id': tool, 'call_id': call_id, 'arguments': {'command': command}}},
                {'task_id': 'task', 'kind': 'tool_result', 'payload': {
                    'tool_id': tool, 'call_id': call_id, 'success': True,
                    'output': {'output': {'exit_code': 0, 'stdout': error if call_id == 'decode' else ''}}}},
            ])
        return events

    def test_actual_decoder_error_is_required_even_when_python_catches_it(self):
        events = self.events()
        self.assertTrue(all(check['passed'] for check in self.checks(events)))
        for output in ['{"readable": false}', 'connection refused', '']:
            changed = copy.deepcopy(events)
            changed[-1]['payload']['output']['output']['stdout'] = output
            self.assertFalse(self.checks(changed)[-1]['passed'])

    def test_decoder_error_must_follow_download_of_this_file_in_this_task(self):
        for fault in ['wrong_task', 'wrong_call', 'wrong_file', 'failed_download', 'truncated', 'no_decoder']:
            events = self.events()
            if fault == 'wrong_task':
                events[-1]['task_id'] = 'foreign-task'
            elif fault == 'wrong_call':
                events[-1]['payload']['call_id'] = 'foreign-call'
            elif fault == 'wrong_file':
                events[0]['payload']['arguments']['command'] = events[0]['payload']['arguments']['command'].replace('owned-file', 'foreign-file')
            elif fault == 'failed_download':
                events[1]['payload']['output']['output']['exit_code'] = 1
            elif fault == 'truncated':
                events[-1]['payload']['output']['output']['output_truncated'] = True
            else:
                events[-2]['payload']['arguments']['command'] = 'echo broken.zip'
            self.assertFalse(self.checks(events)[-1]['passed'], fault)

    def test_png_requires_image_decoder_error_and_archive_error_does_not_count(self):
        events = self.events('broken.png', 'OSError: Truncated File Read')
        events[-2]['payload']['arguments']['command'] = "python3 -c \"from PIL import Image; Image.open('/tmp/broken.png')\""
        self.assertTrue(all(check['passed'] for check in self.checks(events, 'broken.png')))
        events[-1]['payload']['output']['output']['stdout'] = 'BadZipFile: File is not a zip file'
        self.assertFalse(self.checks(events, 'broken.png')[-1]['passed'])
