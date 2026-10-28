"""Check a real downloadable text artifact produced by one Agent task."""
import json
from pathlib import Path

from .client import Failure


def check_text_artifact(execution, conversation_id, task_id, filename, expected, checks, session_id):
    if not filename or filename in {'.', '..'} or Path(filename).name != filename or '\\' in filename or not expected:
        raise Failure('invalid_artifact_assertion')
    execution.collect_artifacts(conversation_id)
    inventory = execution.evidence.root / 'artifacts' / conversation_id / 'inventory.json'
    items = json.loads(inventory.read_text())
    matches = [item for item in items if item.get('task_id') == task_id and item.get('file_name') == filename]
    delivered = len(matches) == 1
    checks.append({'session': session_id, 'rule': '交付指定文件', 'passed': delivered,
                   'detail': '' if delivered else 'matching_artifacts:' + str(len(matches))})
    content_ok = False
    if delivered:
        path = (execution.evidence.root / matches[0]['local_path']).resolve()
        if not path.is_relative_to(execution.evidence.root.resolve()):
            raise Failure('unsafe_artifact_path')
        content = path.read_bytes()
        expected_bytes = expected.encode('utf-8')
        content_ok = content in (expected_bytes, expected_bytes + b'\n', expected_bytes + b'\r\n')
    checks.append({'session': session_id, 'rule': '下载文件内容正确', 'passed': content_ok,
                   'detail': '' if content_ok else 'artifact_content_mismatch' if delivered else 'artifact_unavailable'})
    execution.report['validation'] = {'checks': checks}
    execution.checkpoint()
    return delivered
