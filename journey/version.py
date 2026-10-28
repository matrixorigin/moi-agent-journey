"""Identify the observed MOI backend build before and after a functional case."""
import time

from .client import Failure


def read_moi_version(client):
    data = client.product('GET', '/system/versions', expected_code='OK')
    components = data.get('backend_components')
    if not isinstance(components, list):
        raise Failure('invalid_qa_version_response')
    matches = [item for item in components if isinstance(item, dict) and item.get('component') == 'moi-backend']
    if len(matches) != 1:
        raise Failure('qa_backend_version_not_unique')
    item = matches[0]
    if any(not isinstance(item.get(field), str) or not item[field] for field in ('version', 'git_commit')):
        raise Failure('qa_backend_version_missing')
    return {'version': item['version'], 'git_commit': item['git_commit']}


def start_version(execution):
    execution.report['qa_version_start'] = read_moi_version(execution.client)
    execution.checkpoint()


def finish_version(execution):
    if not execution.report.get('qa_version_start'):
        return
    client = execution.client
    previous_deadline = client.deadline
    client.deadline = time.monotonic() + 20
    try:
        end = read_moi_version(client)
        execution.report['qa_version_end'] = end
        if end != execution.report['qa_version_start']:
            execution.report['blocked'] = True
            execution.report['failures'].append({'stage': 'qa_version', 'reason': 'qa_backend_changed_during_case',
                'details': {'start': execution.report['qa_version_start'], 'end': end}})
            if execution.report['status'] == 'passed':
                execution.report['status'] = 'failed'
    except Exception as error:
        execution.report['harness_errors'].append({'stage': 'qa_version', 'reason': type(error).__name__ + ':' + str(error)})
    finally:
        client.deadline = previous_deadline
