"""Read authoritative Agent Builder candidate identities from task output."""
from urllib.parse import urlencode
import time

from .client import Failure, segment
from .hello import task_state


def candidate_outputs(task):
    outputs = []
    for part in task.get('status', {}).get('message', {}).get('parts', []):
        if not isinstance(part, dict) or not isinstance(part.get('data'), dict):
            continue
        wrapper = part['data'].get('output')
        output = wrapper.get('output') if isinstance(wrapper, dict) else None
        if isinstance(output, dict) and output.get('render_type') == 'agent_candidate':
            outputs.append(output)
    return outputs


def candidate_from_task(task, name, workspace):
    """Never infer a candidate ID from the requested name."""
    matches = []
    for output in candidate_outputs(task):
        display = output.get('display', {})
        if (isinstance(display, dict) and display.get('agent_name') == name and
                output.get('workspace_id') == workspace and output.get('mode') == 'create' and
                output.get('agent_id') == display.get('agent_id') and
                all(isinstance(output.get(key), str) and output[key]
                    for key in ('agent_id', 'candidate_version', 'source_digest', 'status'))):
            matches.append({key: output[key] for key in ('agent_id', 'candidate_version', 'source_digest', 'status')})
    if len(matches) > 1:
        raise Failure('candidate_identity_not_unique')
    return matches[0] if matches else None


def cleanup_candidate(execution):
    """Clean only the candidate or Agent whose identity belongs to this case."""
    client = execution.client
    candidate = getattr(execution, 'candidate', None)
    commit = getattr(execution, 'commit', None)
    agent_id = candidate['agent_id'] if candidate else (commit or {}).get('agent_id')
    if not agent_id:
        return
    try:
        query = urlencode({'agent_workspace_id': execution.workspace_id})
        try:
            agent = client.product('GET', execution.root + '/agents/' + segment(agent_id) + '?' + query)
        except Failure as error:
            if error.code != 'http_error' or error.http_status != 404:
                raise
            agent = None
        if agent:
            if (agent.get('id'), agent.get('name'), agent.get('workspace_id')) != (
                    agent_id, execution.agent_name, execution.workspace_id):
                raise Failure('cleanup_agent_identity_mismatch')
            if agent.get('status') != 'archived':
                archived = client.product('PATCH', execution.root + '/agents/' + segment(agent_id) + '?' + query,
                                          {'status': 'archived'})
                if archived.get('status') != 'archived':
                    raise Failure('agent_archive_not_confirmed')
            execution.report['agent_cleanup'] = 'archived'
        elif candidate and candidate.get('status') == 'superseded':
            execution.report['agent_cleanup'] = 'candidate_canceled'
        elif candidate:
            path = (execution.root + '/agent-builder/candidates/' + segment(candidate['agent_id']) +
                    '/candidate-versions/' + segment(candidate['candidate_version']) + '/cancel')
            result = client.product('POST', path, {'source_digest': candidate['source_digest']})
            if (result.get('agent_id') != candidate['agent_id'] or
                    result.get('candidate_version') != candidate['candidate_version'] or
                    result.get('status') != 'superseded'):
                raise Failure('candidate_cancel_not_confirmed')
            execution.report['agent_cleanup'] = 'candidate_canceled'
        else:
            raise Failure('candidate_cleanup_unknown')
    except Exception as error:
        execution.report['agent_cleanup'] = 'incomplete'
        execution.report['harness_errors'].append({'stage': 'cleanup_agent',
            'reason': type(error).__name__ + ':' + str(error)})



def check_candidate_cancel(execution, task, task_id, name, checks, session_id):
    """Prove a wrong digest is rejected and a candidate can be canceled."""
    client = execution.client
    workspace = execution.root.rsplit('/', 1)[-1]
    try:
        candidate = candidate_from_task(task, name, workspace)
    except Failure:
        execution.report['agent_cleanup'] = 'incomplete'
        execution.report['harness_errors'].append({'stage': 'cleanup_agent', 'reason': 'candidate_identity_unverified'})
        raise

    def record(rule, passed, detail=''):
        checks.append({'session': session_id, 'rule': rule, 'passed': passed, 'detail': detail})
        execution.report['validation'] = {'checks': checks}
        execution.checkpoint()

    record('产生指定名称的候选', bool(candidate), '' if candidate else 'candidate_missing_or_mismatched')
    if not candidate:
        if candidate_outputs(task):
            execution.report['harness_errors'].append({'stage': 'cleanup_agent', 'reason': 'candidate_identity_unverified'})
        return False
    execution.candidate, execution.commit = candidate, None
    execution.agent_name, execution.workspace_id = name, workspace
    execution.evidence.write('candidate.json', candidate)
    record('候选有效', candidate['status'] == 'valid', '' if candidate['status'] == 'valid' else candidate['status'])
    agent_path = (execution.root + '/agents/' + segment(candidate['agent_id']) + '?' +
                  urlencode({'agent_workspace_id': workspace}))
    cancel_path = (execution.root + '/agent-builder/candidates/' + segment(candidate['agent_id']) +
                   '/candidate-versions/' + segment(candidate['candidate_version']) + '/cancel')

    def no_agent():
        try:
            client.product('GET', agent_path)
            return False
        except Failure as error:
            if error.code == 'http_error' and error.http_status == 404:
                return True
            raise

    def readback(label):
        current = client.rpc(execution.selector, 'tasks/get', {'id': task_id})
        task_state(current, task['contextId'], task_id)
        execution.evidence.write('candidate-' + label + '.json', current)
        current_candidate = candidate_from_task(current, name, workspace)
        if not current_candidate:
            raise Failure('candidate_readback_missing')
        if any(current_candidate[key] != candidate[key]
               for key in ('agent_id', 'candidate_version', 'source_digest')):
            raise Failure('candidate_readback_identity_changed')
        execution.candidate = current_candidate
        return current_candidate

    try:
        absent = no_agent()
        record('确认前无已发布智能体', absent, '' if absent else 'agent_already_published')
        if candidate['status'] != 'valid':
            return True
        wrong_digest = 'sha256:' + ('0' * 64 if candidate['source_digest'] != 'sha256:' + '0' * 64 else '1' * 64)
        try:
            client.product('POST', cancel_path, {'source_digest': wrong_digest})
            wrong_status = 200
        except Failure as error:
            if error.code != 'http_error' or error.http_status is None:
                raise
            wrong_status = error.http_status
        record('错误摘要被拒绝', wrong_status == 409, '' if wrong_status == 409 else 'http_status:' + str(wrong_status))
        current = readback('after-invalid-digest')
        unchanged = (current['status'] == 'valid' and current['source_digest'] == candidate['source_digest'])
        record('错误摘要未改变候选', unchanged, '' if unchanged else 'candidate_state_changed')
        if unchanged:
            try:
                canceled = client.product('POST', cancel_path, {'source_digest': candidate['source_digest']})
                confirmed = (canceled.get('agent_id') == candidate['agent_id'] and
                             canceled.get('candidate_version') == candidate['candidate_version'] and
                             canceled.get('status') == 'superseded')
                record('有效候选可取消', confirmed, '' if confirmed else 'candidate_cancel_not_confirmed')
            except Failure as error:
                if error.code != 'http_error' or error.http_status is None:
                    raise
                record('有效候选可取消', False, 'http_status:' + str(error.http_status))
        else:
            record('有效候选可取消', False, 'candidate_not_valid_after_invalid_digest')
        final = readback('after-cancel')
        record('取消状态可回读', final['status'] == 'superseded',
               '' if final['status'] == 'superseded' else 'status:' + final['status'])
        absent = no_agent()
        record('取消后无已发布智能体', absent, '' if absent else 'agent_present_after_cancel')
        return True
    finally:
        # Cleanup is independent of the case verdict and only uses the observed identity.
        prior_deadline = client.deadline
        client.deadline = time.monotonic() + 30
        try:
            try:
                readback('cleanup')
            except Exception as error:
                execution.report['agent_cleanup'] = 'incomplete'
                execution.report['harness_errors'].append({'stage': 'cleanup_agent',
                    'reason': 'candidate_readback:' + type(error).__name__})
            else:
                cleanup_candidate(execution)
        finally:
            client.deadline = prior_deadline
