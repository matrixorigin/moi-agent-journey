"""Account for every configured case, including missing artifacts and killed runners."""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import statistics
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from .catalog import ROOT, case_executor, is_functional_case, journey_label, load_case, load_suite, select_suites
from .diagnostics import analyze_events, success_rate
from .evidence import Evidence, configured_secrets, utc_now,TOKEN_BYTES
from .outcomes import assess
from .native_skill_stages import LABELS as NATIVE_STAGE_LABELS
from .rag_report import (aggregate_lines as rag_aggregate_lines, case_lines as rag_case_lines,
                         summarize_rag, valid_rag_result)

FINAL = {"passed", "failed", "timeout", "error"}
AGENT_LABELS = {'completed': '请求正常结束', 'canceled': '按请求取消', 'abnormal': '请求异常', 'timeout': '超时', 'unknown': '无法确认'}
TASK_LABELS = {'completed': '正常结束并交付', 'abnormal': '非正常结束', 'timeout': '超时',
               'canceled': '按请求取消', 'not_delivered': '未正常交付', 'unknown': '无法确认'}
DELIVERY_LABELS = {'delivered': '已交付', 'partial': '部分交付', 'not_delivered': '未交付',
                   'canceled': '取消已确认', 'incomplete': '交付不完整', 'ambiguous': '交付对象不明确', 'unknown': '无法确认'}
TEST_LABELS = {'completed': '正常完成', 'error': '执行、验收或取证出错'}
FUNCTIONAL_LABELS = {'pass': '通过', 'fail': '不通过', 'blocked': '前置条件不足', 'error': '测试执行错误'}
QUALITY_REASONS = {'validator_error': '验收器异常', 'not_evaluated': '尚未完成验收',
                   'no_validated_delivery': '没有完整可验收的交付'}


def cell(value, limit=1000):
    value = str(value if value is not None else '—').replace('|', '／').replace('\n', ' ').replace('\r', ' ')
    return value if len(value) <= limit else value[:limit] + '…（完整内容见 result.json）'


def _unavailable_tools():
    return {'state': 'unavailable', 'metrics': None, 'errors': [], 'judge_errors': [], 'runtime_sessions': {}}


def mark_expected_tool_errors(analysis, checks):
    """Mark only the exact error observation verified by a negative test."""
    expected = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        refs = check.get('expected_tool_errors', [])
        if not isinstance(refs, list):
            raise ValueError('invalid_expected_tool_error')
        ref = check.get('expected_tool_error')
        if ref is not None:
            refs = [*refs, ref]
        for ref in refs:
            if (not isinstance(ref, dict) or set(ref) != {'task_id', 'call_id', 'kind', 'code'} or
                    any(not isinstance(value, str) or not value for value in ref.values())):
                raise ValueError('invalid_expected_tool_error')
            expected.append(ref)
    for ref in expected:
        matches = [item for call in analysis['errors']
                   if call.get('task_id') == ref['task_id'] and call.get('call_id') == ref['call_id']
                   for item in call['errors'] if item.get('kind') == ref['kind'] and
                   str(item.get('code')) == ref['code']]
        if not matches:
            raise ValueError('expected_tool_error_not_in_events')
        for item in matches:
            item['expected'] = True


def case_summary(result):
    test_status = TEST_LABELS[result['test_status']]
    agent, delivery, quality, tools = (result[key] for key in ('agent', 'delivery', 'quality', 'tools'))
    agent_delivery = bool(delivery.get('sessions')) and all(
        session.get('delivery_kind') == 'agent' for session in delivery['sessions'])
    answer_delivery = bool(delivery.get('sessions')) and all(
        session.get('delivery_kind') == 'answer' for session in delivery['sessions'])
    candidate_delivery = bool(delivery.get('sessions')) and all(
        session.get('delivery_kind') == 'candidate' for session in delivery['sessions'])
    file_delivery = bool(delivery.get('sessions')) and all(
        session.get('delivery_kind') == 'file' for session in delivery['sessions'])
    cancellation = bool(delivery.get('sessions')) and all(
        session.get('delivery_kind') == 'cancellation' for session in delivery['sessions'])
    delivery_unit = ('智能体交付' if agent_delivery else '答复交付' if answer_delivery else
                     '候选交付' if candidate_delivery else '文件交付' if file_delivery else '取消操作' if cancellation else '会话交付')
    delivery_line = (f"- 取消操作：{'已确认' if delivery['state'] == 'canceled' else '未确认'}；"
                     f"完成 {delivery['completed_sessions']}/{delivery['expected_sessions']}。") if cancellation else (
                     f"- 交付：{DELIVERY_LABELS[delivery['state']]}；完成{delivery_unit} "
                     f"{delivery['completed_sessions']}/{delivery['expected_sessions']}。")
    lines = [f"# {cell(result['case_id'])} {cell(result['title'])}", '',
             f"**测试框架：{test_status}**", '',
             f"- 任务结果：{TASK_LABELS[result['task']['state']]}。",
             f"- 智能体：{AGENT_LABELS[agent['state']]}；完成请求轮次 {agent['completed_turns']}/{agent['expected_turns']}。",
             delivery_line,
             f"- 工作区：{cell(journey_label(load_suite(result['workspace'])))}。",
             f"- 端到端任务耗时：{cell(result.get('task_seconds'))} 秒；准备、QA、验收和清理合计：{cell(result.get('total_seconds'))} 秒。",
             f"- 语义评审耗时：{cell(result.get('semantic_judge_seconds'))} 秒；不计入端到端任务耗时。"]
    if result.get('functional'):
        lines.insert(4, f"- 功能验收：{FUNCTIONAL_LABELS[result['functional']['state']]}。")
        before, after = result.get('qa_version_start'), result.get('qa_version_end')
        if isinstance(before, dict) and isinstance(after, dict):
            lines.append(f"- QA MOI 版本：开始 {cell(before.get('version'))} / {cell(before.get('git_commit'))}；"
                         f"结束 {cell(after.get('version'))} / {cell(after.get('git_commit'))}。")
        else:
            lines.append('- QA MOI 版本：未完整获取。')
    for item in result.get('retained_inputs', []):
        lines.append(f"- 测试输入：{cell(item.get('fixture'))}（文件 ID：{cell(item.get('file_id'))}），"
                     '作为会话附件保留在 QA。')
    if quality['state'] in {'checked', 'partial'}:
        qualifier = ('部分检查（任务未全部结束）' if quality.get('reason') == 'unfinished_turns' else
                     '部分检查（验收器异常）') if quality['state'] == 'partial' else '检查'
        lines.append(f"- {'功能验收' if cancellation else '交付质量'}：{qualifier}通过 {quality['passed_checks']}/{quality['total_checks']}，"
                     f"未通过 {len(quality['failed_checks'])} 项；判据仍需结合证据复核。")
    else:
        lines.append(f"- 交付质量：无法评价（{cell(QUALITY_REASONS.get(quality.get('reason'), quality.get('reason')))}）。")
    if tools['state'] == 'available':
        metrics = tools['metrics']
        denominator = metrics['succeeded'] + metrics['failed']
        rate = f"{metrics['success_rate']:.1%}" if metrics['success_rate'] is not None else '不可计算'
        lines.append(f"- 工具：识别到 {metrics['calls']} 次调用、{metrics['tool_rounds']} 个可识别轮次；"
                     f"成功 {metrics['succeeded']}、失败 {metrics['failed']}、跳过 {metrics['skipped']}、"
                     f"结果不明确 {metrics['unknown']}。调用成功率 {rate}（{metrics['succeeded']}/{denominator}）。")
        if metrics['round_unmapped_calls']:
            lines.append(f"- 其中 {metrics['round_unmapped_calls']} 次调用无法关联到模型轮次。")
        if metrics['unidentified_events']:
            lines.append(f"- {metrics['unidentified_events']} 条工具事件缺少调用 ID，无法准确去重计数。")
        if not tools['complete']:
            lines.append('- 工具统计仅基于已采集到的部分事件。')
        lines.append('- 调用成功率依据公开事件中的执行状态；跳过及结果不明确的调用不计入分母，不代表工具结果的业务正确性。')
        expected_errors = sum(any(item.get('expected') is True for item in call.get('errors', []))
                              for call in tools.get('errors', []))
        if expected_errors:
            lines.append(f'- 其中 {expected_errors} 次调用的错误由用例主动触发；错误明细已标记，仍计入实际调用成功率。')
        if metrics['by_tool']:
            lines += ['', '| 工具 | 调用 | 成功 | 失败 | 跳过 | 不明确 | 成功率 |', '|---|---:|---:|---:|---:|---:|---:|']
            for item in metrics['by_tool']:
                item_rate = f"{item['success_rate']:.1%}" if item['success_rate'] is not None else '—'
                lines.append(f"| {cell(item['tool'])} | {item['calls']} | {item['succeeded']} | {item['failed']} | "
                             f"{item['skipped']} | {item['unknown']} | {item_rate} |")
    else:
        lines.append('- 工具：没有可用的事件数据。')
    if result.get('native_skill_stages'):
        lines += native_stage_summary(result['native_skill_stages'])
    failures = result.get('failures', [])
    errors = result.get('harness_errors', [])
    if failures or errors:
        lines += ['', '## 任务和测试框架事件', '']
        for kind, items in [('任务', failures), ('执行/验收/取证', errors)]:
            for item in items:
                detail = item.get('details')
                if isinstance(detail, (dict, list)):
                    detail = json.dumps(detail, ensure_ascii=False)
                suffix = f"；详情：{cell(detail)}" if detail else ''
                lines.append(f"- {kind} / {cell(item.get('stage'))}：{cell(item.get('reason'))}{suffix}")
    checks = quality.get('failed_checks', [])
    if checks:
        lines += ['', f"## 未通过的验收项（{len(checks)} 项）", '']
        for check in checks:
            detail = check.get('detail') or '验收器未提供具体原因，需结合原始产物复核'
            lines.append(f"- {cell(check.get('session'))} / {cell(check.get('rule'))}：{cell(detail)}")
    tasks = result.get('tasks', [])
    conversations = result.get('conversations', [])
    if tasks or conversations:
        lines += ['', '## 会话与 trace', '',
                  '| 阶段 | MOI 会话 ID | Task ID | Trace ID | 运行时 Session ID | 耗时（秒） |',
                  '|---|---|---|---|---|---:|']
        for task in tasks:
            session = tools.get('runtime_sessions', {}).get(task.get('task_id'), {})
            runtime_ids = ', '.join(session.get('ids', [])) or ('接口已脱敏' if session.get('redacted') else '未获取')
            lines.append(f"| {cell(task.get('phase'))} | {cell(task.get('conversation_id'))} | "
                         f"{cell(task.get('task_id'))} | {cell(task.get('trace_id'))} | "
                         f"{cell(runtime_ids)} | {cell(task.get('seconds'))} |")
        task_conversations = {task.get('conversation_id') for task in tasks}
        for conversation in conversations:
            if conversation.get('id') not in task_conversations:
                lines.append(f"| {cell(conversation.get('purpose'))} | {cell(conversation.get('id'))} | "
                             '未获取 | 未获取 | 未获取 | — |')
        lines += ['', f"事件与 trace 采集完整：{'是' if result.get('capture_complete') else '否'}。"]
    else:
        lines += ['', '## 会话与 trace', '', '未获取会话或 trace ID。']
    if result.get('admission_failures'):
        lines += ['', '## 任务受理失败', '',
                  '以下请求被运行时明确拒绝，已核对会话和任务列表，确认没有创建新任务。请求 trace 不代表运行时 task trace。', '',
                  '| 阶段 | MOI 会话 ID | 请求 Trace ID | 错误 |', '|---|---|---|---|']
        for item in result['admission_failures']:
            headers = {key.lower(): value for key, value in item.get('trace_headers', {}).items()}
            lines.append(f"| {cell(item.get('phase'))} | {cell(item.get('conversation_id'))} | "
                         f"{cell(headers.get('x-trace-id'))} | {cell(json.dumps(item.get('error'), ensure_ascii=False))} |")
    for label, observed in [('智能体', tools.get('errors', [])), ('评审', tools.get('judge_errors', []))]:
        if not observed:
            continue
        lines += ['', f"## {label}工具错误（{len(observed)} 条记录）", '',
                  '| 阶段 | 工具 | 参数 | 错误 | 事件序号 |', '|---|---|---|---|---|']
        for call in observed:
            args = json.dumps(call.get('arguments'), ensure_ascii=False) if call.get('arguments') is not None else '未获取'
            details = '; '.join(('［预期测试错误］' if item.get('expected') is True else '') +
                                str(item.get('code') or item.get('kind')) + ': ' + str(item.get('detail') or '')
                                for item in call['errors'])
            lines.append(f"| {cell(call.get('phase'))} | {cell(call.get('tool'))} | {cell(args)} | "
                         f"{cell(details)} | {cell(', '.join(map(str, call['seqs'])))} |")
        lines += ['', '表中参数和错误可能截断；原始已脱敏事件见 events.jsonl。']
    lines += ['', '完整判词、逐轮事件与产物见本 case artifact 中的 result.json、events.jsonl 和其他证据文件；'
              '公开包中被隔离的文件见 publication.json。', '',
              '智能体问题和质量检查不改变测试框架状态；质量判据需结合原始证据复核。', '']
    lines.extend(rag_case_lines(result, cell))
    return '\n'.join(lines)


def native_stage_summary(stages):
    lines = ['', '## 连续会话阶段', '',
             '阶段共享同一个智能体和会话；依赖阻塞的轮次没有发送，不计作新的功能失败。', '',
             '| 阶段 | 结果 | 已验收/计划轮次 | 已记录耗时（秒） | 未执行原因 |',
             '|---|---|---:|---:|---|']
    for stage in stages:
        reasons = [turn['phase'] + ': ' + ', '.join(turn.get('blocked_by', []))
                   for turn in stage['turns'] if turn['status'] == 'dependency_blocked']
        reasons += [turn['phase'] + ': ' + turn.get('reason', '')
                    for turn in stage['turns'] if turn['status'] in {'not_run', 'error'}]
        lines.append(f"| {cell(stage['id'])} · {cell(stage['title'])} | {NATIVE_STAGE_LABELS[stage['status']]} | "
                     f"{stage.get('completed_turns', 0)}/{len(stage['turns'])} | {cell(stage.get('seconds'))} | "
                     f"{cell('; '.join(reasons))} |")
    lines += ['', '| 阶段 / 轮次 | 结果 | Trace ID | 未通过的验收项 |', '|---|---|---|---|']
    for stage in stages:
        for turn in stage['turns']:
            lines.append(f"| {cell(stage['id'])} / {cell(turn['phase'])} | {NATIVE_STAGE_LABELS[turn['status']]} | "
                         f"{cell(turn.get('trace_id'))} | {cell('; '.join(turn.get('failed_checks', [])))} |")
    return lines


def finish_case(case_id, directory):
    case = load_case(case_id)
    evidence = Evidence(Path(directory) / case_id, configured_secrets())
    path = evidence.root / "result.json"
    if path.exists():
        result = json.loads(path.read_text())
        if ('status' not in result and result.get('case_id') == case_id and
                result.get('workspace') == case['workspace'] and _valid_case_result(result)):
            result = evidence.clean(result)
            evidence.write('result.json', result)
            (evidence.root / 'case-summary.md').write_text(evidence.text(case_summary(result)), encoding='utf-8')
            return result
    else:
        result = {"case_id": case_id, "workspace": case["workspace"], "title": case["title"],
                  "status": "error", "tasks": [], "failures": [], "harness_errors": []}
        prep = evidence.root / "preparation.json"
        reason = json.loads(prep.read_text()).get("reason", "execution_did_not_start") if prep.exists() else "preparation_did_not_finish"
        result["harness_errors"].append({"stage": "setup", "reason": reason})
    result.setdefault('case_id', case_id)
    result.setdefault('workspace', case['workspace'])
    result.setdefault('title', case['title'])
    result.setdefault('tasks', [])
    result.setdefault('failures', [])
    result.setdefault('harness_errors', [])
    raw_status = result.get('status')
    if case_executor(case) == 'knowledge' and not valid_rag_result(result.get('rag')):
        result['harness_errors'].append({'stage': 'evidence', 'reason': 'knowledge_report_missing_or_invalid'})
        result['rag'] = {'corpus': case['rag']['corpus'], 'answers': [],
            'expected_answers': sum('rag_reference' in turn for session in case['sessions'] for turn in session['turns'])}
    if raw_status not in FINAL:
        result["harness_errors"].append({"stage": result.get("stage"), "reason": "case_did_not_reach_final_verdict"})
    if raw_status == 'error' and not result['harness_errors']:
        result['harness_errors'].append({'stage': result.get('stage'), 'reason': 'case_error_without_details'})
    if (result.get("tasks") or raw_status == "passed") and not result.get("capture_complete"):
        result["harness_errors"].append({"stage": "evidence", "reason": "incomplete_trace_or_events"})
    if raw_status == 'passed' and not result.get('validation', {}).get('checks'):
        result['harness_errors'].append({'stage': 'validation', 'reason': 'quality_checks_missing'})
    if raw_status == 'passed' and any(session.get('delivery_kind') == 'agent' for session in case.get('sessions', [])):
        by_id = {session.get('id'): session for session in result.get('sessions', [])}
        if any(not isinstance(by_id.get(session['id'], {}).get('created_agent'), dict) or
               by_id[session['id']]['created_agent'].get('verified') is not True
               for session in case['sessions'] if session.get('delivery_kind') == 'agent'):
            result['harness_errors'].append({'stage': 'validation', 'reason': 'verified_agent_missing'})
    if raw_status == 'passed' and any(session.get('delivery_kind') == 'answer' for session in case.get('sessions', [])):
        by_id = {session.get('id'): session for session in result.get('sessions', [])}
        if any(by_id.get(session['id'], {}).get('answer_delivered') is not True
               for session in case['sessions'] if session.get('delivery_kind') == 'answer'):
            result['harness_errors'].append({'stage': 'validation', 'reason': 'delivered_answer_missing'})
    if raw_status == 'passed' and any(session.get('delivery_kind') == 'candidate' for session in case.get('sessions', [])):
        by_id = {session.get('id'): session for session in result.get('sessions', [])}
        if any(by_id.get(session['id'], {}).get('candidate_verified') is not True
               for session in case['sessions'] if session.get('delivery_kind') == 'candidate'):
            result['harness_errors'].append({'stage': 'validation', 'reason': 'verified_candidate_missing'})
    if raw_status == 'passed' and any(session.get('delivery_kind') == 'file' for session in case.get('sessions', [])):
        by_id = {session.get('id'): session for session in result.get('sessions', [])}
        if any(by_id.get(session['id'], {}).get('artifact_delivered') is not True
               for session in case['sessions'] if session.get('delivery_kind') == 'file'):
            result['harness_errors'].append({'stage': 'validation', 'reason': 'delivered_artifact_missing'})
    if raw_status == 'passed' and any(session.get('delivery_kind') == 'cancellation' for session in case.get('sessions', [])):
        by_id = {session.get('id'): session for session in result.get('sessions', [])}
        if any(by_id.get(session['id'], {}).get('cancellation_confirmed') is not True
               for session in case['sessions'] if session.get('delivery_kind') == 'cancellation'):
            result['harness_errors'].append({'stage': 'validation', 'reason': 'cancellation_not_confirmed'})
    if raw_status == 'passed' and is_functional_case(case):
        if any(not isinstance(result.get(key), dict) or
               any(not isinstance(result[key].get(field), str) or not result[key][field]
                   for field in ('version', 'git_commit'))
               for key in ('qa_version_start', 'qa_version_end')):
            result['harness_errors'].append({'stage': 'qa_version', 'reason': 'qa_backend_version_missing'})
        elif result['qa_version_start'] != result['qa_version_end']:
            result['harness_errors'].append({'stage': 'qa_version', 'reason': 'qa_version_verdict_inconsistent'})
        expected = Counter((session['id'] + ':' + turn['phase'], turn.get('expected_state', 'completed'))
                           for session in case['sessions'] for turn in session['turns'])
        completed = Counter((task.get('phase'), task.get('state')) for task in result.get('tasks', [])
                            if task.get('capture_complete'))
        if expected != completed:
            result['harness_errors'].append({'stage': 'evidence', 'reason': 'functional_turns_not_fully_captured'})
    if (raw_status == 'failed' and any(item.get('reason') == 'acceptance_checks_failed'
                                       for item in result['failures']) and
            not result.get('validation', {}).get('checks')):
        result['harness_errors'].append({'stage': 'validation', 'reason': 'quality_checks_missing'})
    checks = result.get('validation', {}).get('checks', [])
    if raw_status == 'passed' and case_executor(case) == 'api':
        expected_checks = sum(session.get('expected_checks', sum(1 + len(turn.get('assertions', []))
                                  for turn in session['turns'])) for session in case['sessions'])
        if len(checks) != expected_checks:
            result['harness_errors'].append({'stage': 'validation', 'reason': 'functional_checks_incomplete'})
    if checks and any(not isinstance(item, dict) or type(item.get('passed')) is not bool for item in checks):
        result['harness_errors'].append({'stage': 'validation', 'reason': 'invalid_quality_checks'})
        result.pop('validation', None)
    elif raw_status == 'passed' and any(not item['passed'] for item in checks):
        result['harness_errors'].append({'stage': 'validation', 'reason': 'contradictory_quality_verdict'})
    elif (raw_status == 'failed' and checks and
          any(item.get('reason') == 'acceptance_checks_failed' for item in result['failures']) and
          all(item['passed'] for item in checks)):
        result['harness_errors'].append({'stage': 'validation', 'reason': 'contradictory_quality_verdict'})
    events = evidence.root / 'events.jsonl'
    if events.exists():
        try:
            analysis = analyze_events(events, result.get('tasks', []))
            mark_expected_tool_errors(analysis, checks)
            result['tools'] = {'state': 'available', 'complete': bool(result.get('capture_complete')), **analysis}
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            result['harness_errors'].append({'stage': 'evidence', 'reason': 'invalid_event_log:' + str(error)})
            result['tools'] = _unavailable_tools()
    elif any(task.get('event_count', 0) for task in result.get('tasks', [])):
        result['harness_errors'].append({'stage': 'evidence', 'reason': 'recorded_events_missing'})
        result['tools'] = _unavailable_tools()
    else:
        result['tools'] = _unavailable_tools()
    result['task'], result['agent'], result['delivery'], result['quality'] = assess(case, result, raw_status)
    result['test_status'] = 'error' if result['harness_errors'] else 'completed'
    if is_functional_case(case):
        if result.get('blocked'):
            functional_state = 'blocked'
        elif result['test_status'] == 'error':
            functional_state = 'error'
        elif raw_status == 'passed':
            functional_state = 'pass'
        else:
            functional_state = 'fail'
        result['functional'] = {'state': functional_state}
    preparation=evidence.root/'preparation.json'
    if preparation.exists():
        result['preparation_seconds']=json.loads(preparation.read_text()).get('seconds')
    durations=[value for value in [result.get('preparation_seconds'),result.get('elapsed_seconds'),
        result.get('validation',{}).get('seconds')] if isinstance(value,(int,float))]
    result['total_seconds']=round(sum(durations),3) if durations else None
    result['github_run_id']=os.environ.get('GITHUB_RUN_ID')
    result['github_run_attempt']=os.environ.get('GITHUB_RUN_ATTEMPT')
    result['workflow_commit']=os.environ.get('GITHUB_SHA')
    specification=json.dumps(case,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    result['case_spec_sha256']=hashlib.sha256(specification).hexdigest()
    result["finalized_at"] = utc_now()
    result.pop('status', None)
    result = evidence.clean(result)
    evidence.write("result.json", result)
    (evidence.root / 'case-summary.md').write_text(evidence.text(case_summary(result)), encoding='utf-8')
    return result


class ArtifactRejected(ValueError):
    pass


def inspect_payload(data, secrets, depth=0,name=''):
    """A failure is an explicit withholding decision, never permission to publish."""
    if any(s.encode() in data for s in secrets if s) or TOKEN_BYTES.search(data):
        raise ArtifactRejected("credential_detected_in_artifact")
    if re.search(r'\.(?:z\d{2,}|zip\.(?:part)?\d+)$',name,re.I):
        raise ArtifactRejected('split_archive_volume_not_inspectable')
    container=Path(name).suffix.lower() in {'.zip','.docx','.xlsx','.pptx','.xlsm','.docm','.pptm'}
    if container or data.startswith(b'PK\x03\x04') or zipfile.is_zipfile(io.BytesIO(data)):
        if depth>=5:raise ArtifactRejected("artifact_nested_archive_limit")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(f.file_size for f in archive.infolist())>1_000_000_000:
                    raise ArtifactRejected("artifact_archive_expansion_limit")
                for member in archive.infolist():
                    inspect_payload(member.filename.encode(),secrets,depth+1)
                    if not member.is_dir():inspect_payload(archive.read(member),secrets,depth+1,member.filename)
        except ArtifactRejected:
            raise
        except (zipfile.BadZipFile,ValueError,RuntimeError,NotImplementedError) as error:
            raise ArtifactRejected("archive_not_inspectable:"+type(error).__name__+":"+str(error)) from error


def scan(directory, secrets):
    """Strict diagnostic scan, also used by offline tests."""
    for file in Path(directory).rglob("*"):
        if file.is_symlink():
            raise ArtifactRejected("artifact_symlink_not_allowed")
        if file.is_file():
            inspect_payload(file.read_bytes(),secrets,name=file.name)


def _time_stats(values):
    values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)
              and math.isfinite(value) and value >= 0]
    if not values:
        return {'count': 0, 'mean': None, 'median': None, 'min': None, 'max': None}
    return {'count': len(values), 'mean': round(statistics.mean(values), 3),
            'median': round(statistics.median(values), 3), 'min': min(values), 'max': max(values)}


def _group_summary(rows):
    tasks = Counter(row['task']['state'] for row in rows)
    agents = Counter(row['agent']['state'] for row in rows)
    deliveries = Counter(row['delivery']['state'] for row in rows)
    quality = Counter(row['quality']['state'] if row['quality']['state'] != 'checked' else
                      ('issues' if row['quality']['failed_checks'] else 'all_checks_passed') for row in rows)
    metrics = [row['tools']['metrics'] for row in rows if row['tools']['state'] == 'available']
    tools = {key: sum(item[key] for item in metrics)
             for key in ('calls', 'succeeded', 'failed', 'skipped', 'unknown', 'tool_rounds',
                         'round_unmapped_calls', 'unidentified_events')}
    grouped = defaultdict(Counter)
    for item in metrics:
        for entry in item['by_tool']:
            for key in ('calls', 'succeeded', 'failed', 'skipped', 'unknown'):
                grouped[entry['tool']][key] += entry[key]
    per_tool = []
    for name, counts in sorted(grouped.items()):
        item = {'tool': name, **{key: counts[key] for key in ('calls', 'succeeded', 'failed', 'skipped', 'unknown')}}
        item['success_rate'] = success_rate(item['succeeded'], item['failed'])
        per_tool.append(item)
    tools.update({'success_rate': success_rate(tools['succeeded'], tools['failed']),
                  'cases_unavailable': len(rows) - len(metrics),
                  'cases_incomplete': sum(row['tools']['state'] == 'available' and not row['tools']['complete']
                                          for row in rows), 'by_tool': per_tool})
    functional = Counter(row['functional']['state'] for row in rows if row.get('functional'))
    completion_rows = [row for row in rows if not row.get('expected_cancellation')]
    completed_count = sum(row['task']['state'] == 'completed' for row in completion_rows)
    summary = {'total': len(rows),
            'test_errors': sum(row['test_status'] == 'error' for row in rows),
            'functional': {key: functional[key] for key in ('pass', 'fail', 'blocked', 'error')},
            'functional_pass_rate': functional['pass'] / sum(functional.values()) if functional else None,
            'task': {key: tasks[key] for key in ('completed', 'canceled', 'abnormal', 'timeout', 'not_delivered', 'unknown')},
            'completion_eligible': len(completion_rows),
            'completion_confirmed': completed_count,
            'confirmed_completion_rate': completed_count / len(completion_rows) if completion_rows else None,
            'agent': {key: agents[key] for key in ('completed', 'canceled', 'abnormal', 'timeout', 'unknown')},
            'delivery': {key: deliveries[key] for key in
                         ('delivered', 'canceled', 'partial', 'not_delivered', 'incomplete', 'ambiguous', 'unknown')},
            'quality': {key: quality[key] for key in ('all_checks_passed', 'issues', 'partial', 'unavailable')},
            'task_seconds': _time_stats(row.get('task_seconds') for row in rows),
            'completed_task_seconds': _time_stats(row.get('task_seconds') for row in rows
                                                  if row['task']['state'] == 'completed'),
            'tools': tools}
    if any(isinstance(row.get('rag'), dict) for row in rows):
        summary['rag'] = summarize_rag(rows)
    return summary


def _qa_versions(rows):
    builds, missing = defaultdict(set), 0
    for row in rows:
        versions = [row.get('qa_version_start'), row.get('qa_version_end')]
        if any(not isinstance(value, dict) or
               any(not isinstance(value.get(key), str) or not value[key] for key in ('version', 'git_commit'))
               for value in versions):
            missing += 1
        for value in versions:
            if isinstance(value, dict) and all(isinstance(value.get(key), str) and value[key]
                                               for key in ('version', 'git_commit')):
                builds[(value['version'], value['git_commit'])].add(row['case_id'])
    state = 'mixed' if len(builds) > 1 else 'unknown' if missing or not builds else 'consistent'
    return {'state': state, 'missing_cases': missing,
            'builds': [{'version': version, 'git_commit': commit, 'cases': sorted(cases)}
                       for (version, commit), cases in sorted(builds.items())]}


def _missing_case(case, workspace, reason):
    result = {'case_id': case['id'], 'workspace': workspace, 'title': case['title'], 'test_status': 'error',
            'tasks': [], 'failures': [], 'harness_errors': [{'stage': 'aggregation', 'reason': reason}],
            'task': {'state': 'unknown'},
            'agent': {'state': 'unknown', 'completed_turns': 0, 'expected_turns': 0},
            'delivery': {'state': 'unknown', 'completed_sessions': 0, 'expected_sessions': 0},
            'quality': {'state': 'unavailable', 'passed_checks': None, 'total_checks': None, 'failed_checks': []},
            'tools': _unavailable_tools()}
    if is_functional_case(case):
        result['functional'] = {'state': 'error'}
    if case_executor(case) == 'knowledge':
        result['rag'] = {'corpus': case['rag']['corpus'], 'answers': [],
            'expected_answers': sum('rag_reference' in turn for session in case['sessions'] for turn in session['turns'])}
    return result


def _valid_case_result(result):
    if (not isinstance(result, dict) or not isinstance(result.get('title'), str) or
            not isinstance(result.get('test_status'), str) or
            result['test_status'] not in TEST_LABELS):
        return False
    required = {'task': TASK_LABELS, 'agent': AGENT_LABELS, 'delivery': DELIVERY_LABELS}
    if any(not isinstance(result.get(key), dict) or not isinstance(result[key].get('state'), str) or
           result[key]['state'] not in labels
           for key, labels in required.items()):
        return False
    if ('functional' in result and
            (not isinstance(result['functional'], dict) or result['functional'].get('state') not in FUNCTIONAL_LABELS)):
        return False
    if result.get('functional', {}).get('state') == 'pass':
        versions = [result.get('qa_version_start'), result.get('qa_version_end')]
        if (result['test_status'] != 'completed' or
                any(not isinstance(value, dict) or
                    any(not isinstance(value.get(key), str) or not value[key] for key in ('version', 'git_commit'))
                    for value in versions) or versions[0] != versions[1]):
            return False
    for key, names in [('agent', ('completed_turns', 'expected_turns')),
                       ('delivery', ('completed_sessions', 'expected_sessions'))]:
        if (any(type(result[key].get(name)) is not int or result[key][name] < 0 for name in names) or
                result[key][names[0]] > result[key][names[1]]):
            return False
    quality, tools = result.get('quality'), result.get('tools')
    if (not isinstance(quality, dict) or not isinstance(quality.get('state'), str) or
            quality['state'] not in {'checked', 'partial', 'unavailable'} or
            not isinstance(quality.get('failed_checks'), list) or not isinstance(tools, dict) or
            not isinstance(tools.get('state'), str) or tools['state'] not in {'available', 'unavailable'} or
            not isinstance(tools.get('errors'), list) or
            not isinstance(tools.get('judge_errors'), list) or not isinstance(tools.get('runtime_sessions'), dict)):
        return False
    if quality['state'] in {'checked', 'partial'} and any(type(quality.get(key)) is not int or quality[key] < 0
                                             for key in ('passed_checks', 'total_checks')):
        return False
    if quality['state'] in {'checked', 'partial'} and (quality['total_checks'] == 0 or
            quality['passed_checks'] + len(quality['failed_checks']) != quality['total_checks']):
        return False
    if any(not isinstance(call, dict) or not isinstance(call.get('task_id'), str) or not call['task_id'] or
           not isinstance(call.get('call_id'), str) or not call['call_id'] or
           not isinstance(call.get('errors'), list) or not isinstance(call.get('seqs'), list) or
           any(not isinstance(item, dict) for item in call['errors'])
           for call in tools['errors'] + tools['judge_errors']):
        return False
    if tools['state'] == 'available':
        metrics = tools.get('metrics')
        keys = ('calls', 'succeeded', 'failed', 'skipped', 'unknown', 'tool_rounds',
                'round_unmapped_calls', 'unidentified_events')
        if (not isinstance(metrics, dict) or type(tools.get('complete')) is not bool or
                any(type(metrics.get(key)) is not int or metrics[key] < 0 for key in keys)):
            return False
        if metrics['calls'] != sum(metrics[key] for key in ('succeeded', 'failed', 'skipped', 'unknown')):
            return False
        if metrics.get('success_rate') != success_rate(metrics['succeeded'], metrics['failed']):
            return False
        if (not isinstance(metrics.get('by_tool'), list) or
                any(not isinstance(item, dict) or not isinstance(item.get('tool'), str) or
                    any(type(item.get(key)) is not int or item[key] < 0
                        for key in ('calls', 'succeeded', 'failed', 'skipped', 'unknown'))
                    for item in metrics['by_tool']) or
                any(item['calls'] != sum(item[key] for key in ('succeeded', 'failed', 'skipped', 'unknown'))
                    for item in metrics['by_tool']) or
                any(item.get('success_rate') != success_rate(item['succeeded'], item['failed'])
                    for item in metrics['by_tool']) or
                any(sum(item[key] for item in metrics['by_tool']) != metrics[key]
                    for key in ('calls', 'succeeded', 'failed', 'skipped', 'unknown'))):
            return False
    return all(isinstance(result.get(key, []), list) and
               all(isinstance(item, dict) for item in result.get(key, []))
               for key in ('tasks', 'conversations', 'failures', 'harness_errors'))


def aggregate(source, destination, workspace='all'):
    config = json.loads((ROOT / 'suites.json').read_text())
    suites_to_run = select_suites(config, workspace)
    if workspace != 'all':
        workspace = suites_to_run[0]['workspace']
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    rows, suites = [], []
    for suite in suites_to_run:
        current = []
        for case_id in suite['cases']:
            case = load_case(case_id)
            file = Path(source) / case_id / 'result.json'
            if file.exists():
                try:
                    result = json.loads(file.read_text(encoding='utf-8'))
                except (OSError, ValueError, UnicodeError):
                    result = _missing_case(case, suite['workspace'], 'case_result_unreadable')
                if not isinstance(result, dict) or result.get('case_id') != case_id or result.get('workspace') != suite['workspace']:
                    result = _missing_case(case, suite['workspace'], 'case_result_identity_mismatch')
                elif (not _valid_case_result(result) or (is_functional_case(case) and not result.get('functional')) or
                      (case_executor(case) == 'knowledge' and not valid_rag_result(result.get('rag')))):
                    result = _missing_case(case, suite['workspace'], 'case_result_incomplete')
            else:
                result = _missing_case(case, suite['workspace'], 'case_artifact_missing')
            sessions = case.get('sessions', [])
            result['expected_cancellation'] = bool(sessions) and all(
                session.get('delivery_kind') == 'cancellation' for session in sessions)
            rows.append(result)
            current.append(result)
        suite_result = {'workspace': suite['workspace'], 'display_name': journey_label(suite), 'concurrency': suite['concurrency'],
                        'status': 'configured' if current else 'empty', **_group_summary(current)}
        if 'coverage_status' in suite:
            suite_result['coverage_status'] = suite['coverage_status']
        if any(is_functional_case(load_case(case_id)) for case_id in suite['cases']):
            suite_result['qa_versions'] = _qa_versions(current)
        suites.append(suite_result)
    totals = _group_summary(rows)
    task_errors, tool_errors = defaultdict(set), defaultdict(set)
    for row in rows:
        for item in row.get('failures', []) + row.get('harness_errors', []):
            task_errors[(str(item.get('stage') or 'unknown'), str(item.get('reason') or 'unknown'))].add(row['case_id'])
        for scope, calls in [('agent', row['tools'].get('errors', [])), ('judge', row['tools'].get('judge_errors', []))]:
            for call in calls:
                for item in call['errors']:
                    tool_errors[(scope, str(call.get('tool') or 'unknown'), str(item.get('code') or item.get('kind')),
                                 item.get('expected') is True)].add(
                        (row['case_id'], call['task_id'], call['call_id']))
    report = {'generated_at': utc_now(), 'workspace_selection': workspace, 'github_run_id': os.environ.get('GITHUB_RUN_ID'),
              'github_run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'), 'commit': os.environ.get('GITHUB_SHA'),
              **totals, 'journeys': suites, 'cases': rows,
              'task_errors': [{'stage': stage, 'reason': reason, 'cases': sorted(cases)}
                              for (stage, reason), cases in sorted(task_errors.items())],
              'tool_errors': [{'scope': scope, 'tool': tool, 'code': code, 'expected': expected, 'records': len(calls),
                               'cases': sorted({case_id for case_id, _, _ in calls})}
                              for (scope, tool, code, expected), calls in sorted(tool_errors.items())],
              'withheld_files': sum(len(row.get('publication', {}).get('withheld_files', [])) for row in rows)}
    (destination / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    with (destination / 'summary.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['workspace', 'case_id', 'title', 'test_status', 'functional_state', 'task_state', 'agent_state', 'delivery_state',
                         'quality_state', 'checks_passed', 'checks_total', 'task_seconds', 'semantic_judge_seconds',
                         'preparation_seconds', 'validation_seconds', 'cleanup_seconds', 'total_seconds',
                         'tool_calls', 'tool_rounds', 'tool_succeeded', 'tool_failed', 'tool_skipped', 'tool_unknown',
                         'unidentified_tool_events',
                         'tool_success_rate', 'conversation_ids', 'trace_ids', 'failure_reasons', 'tool_errors',
                         'judge_tool_errors', 'rag_correctness_rate', 'rag_faithfulness_rate',
                         'rag_citation_support_rate', 'rag_citation_integrity_rate', 'rag_efficiency', 'rag_evaluation_states'])
        for row in rows:
            metrics = row['tools'].get('metrics') or {}
            rag = summarize_rag([row]) if row.get('rag') else None
            writer.writerow([row['workspace'], row['case_id'], row['title'], row['test_status'], row.get('functional', {}).get('state'),
                row['task']['state'], row['agent']['state'],
                row['delivery']['state'], row['quality']['state'], row['quality'].get('passed_checks'),
                row['quality'].get('total_checks'), row.get('task_seconds'), row.get('semantic_judge_seconds'),
                row.get('preparation_seconds'), row.get('validation', {}).get('seconds'), row.get('cleanup_seconds'),
                row.get('total_seconds'), metrics.get('calls'), metrics.get('tool_rounds'), metrics.get('succeeded'),
                metrics.get('failed'), metrics.get('skipped'), metrics.get('unknown'), metrics.get('unidentified_events'),
                metrics.get('success_rate'),
                ';'.join(dict.fromkeys(
                    [str(c['id']) for c in row.get('conversations', []) if c.get('id')] +
                    [str(t['conversation_id']) for t in row.get('tasks', []) if t.get('conversation_id')])),
                ';'.join(str(t['trace_id']) for t in row.get('tasks', []) if t.get('trace_id')),
                json.dumps(row.get('failures', []) + row.get('harness_errors', []), ensure_ascii=False),
                json.dumps(row['tools'].get('errors', []), ensure_ascii=False),
                json.dumps(row['tools'].get('judge_errors', []), ensure_ascii=False),
                *([rag['metrics'][name]['rate'] for name in ('correctness', 'faithfulness', 'citation_support', 'citation_integrity')]
                  if rag else [None] * 4),
                json.dumps(rag['efficiency'], ensure_ascii=False) if rag else None,
                json.dumps(rag['evaluation_states'], ensure_ascii=False) if rag else None])
    task, agent, delivery, quality, tools = (report[key] for key in ('task', 'agent', 'delivery', 'quality', 'tools'))
    rate = f"{tools['success_rate']:.1%}" if tools['success_rate'] is not None else '不可计算'
    completion = f"{report['confirmed_completion_rate']:.1%}" if report['confirmed_completion_rate'] is not None else '不可计算'
    display_names = {suite['workspace']: suite['display_name'] for suite in suites}
    scope_label = 'all' if workspace == 'all' else display_names[workspace]
    lines = ['# MOI agent journeys', '', f'运行范围：{cell(scope_label)}。', '',
        f"**{report['total']} 个任务：非取消类正常结束并交付 {report['completion_confirmed']}/{report['completion_eligible']}（{completion}），"
        f"按请求取消 {task['canceled']}，"
        f"非正常 {task['abnormal']}，"
        f"超时 {task['timeout']}，未正常交付 {task['not_delivered']}，无法确认 {task['unknown']}。**", '',
        '正常结束并交付的比例以本批非取消类 case 为分母，包含结果无法确认的 case；按请求取消单独统计，交付质量另看逐项检查。', '',
        f"智能体请求：正常结束 {agent['completed']}，按请求取消 {agent['canceled']}，异常 {agent['abnormal']}，超时 {agent['timeout']}，"
        f"无法确认 {agent['unknown']}；请求正常结束不代表已交付或质量合格。", '',
        f"交付：完整 {delivery['delivered']}；取消确认 {delivery['canceled']}；部分 {delivery['partial']}；未交付 {delivery['not_delivered']}；"
        f"交付不完整 {delivery['incomplete']}；对象不明确 {delivery['ambiguous']}；无法确认 {delivery['unknown']}。", '',
        f"质量：检查全部符合 {quality['all_checks_passed']}；存在未通过项 {quality['issues']}；"
        f"仅完成部分检查 {quality['partial']}；无法评价 {quality['unavailable']}。检查结果需结合原始证据复核。", '',
        f"工具：{tools['calls']} 次调用、{tools['tool_rounds']} 个可识别轮次；成功 {tools['succeeded']}、"
        f"失败 {tools['failed']}、跳过 {tools['skipped']}、结果不明确 {tools['unknown']}；调用成功率 {rate}。", '',
        f"无法关联调用 ID 的工具事件：{tools['unidentified_events']} 条，不计入调用次数和成功率。", '',
        f"测试框架错误：{report['test_errors']}；无工具事件数据的 case：{tools['cases_unavailable']}；"
        f"工具事件不完整的 case：{tools['cases_incomplete']}；隔离产物：{report['withheld_files']}。", '',
        f"端到端任务耗时（{report['task_seconds']['count']} 个已记录）：中位数 "
        f"{cell(report['task_seconds']['median'])} 秒，最长 {cell(report['task_seconds']['max'])} 秒。", '',
        f"其中正常结束并交付的任务（{report['completed_task_seconds']['count']} 个）：中位数 "
        f"{cell(report['completed_task_seconds']['median'])} 秒。", '',
        '工具成功率依据公开事件中的执行状态；跳过及结果不明确的调用不计入分母。用例主动触发的错误仍计入成功率，并在错误统计中标记；该比例不代表业务正确率。', '',
        '| 工作区 | Case | 框架 | 功能验收 | 任务 | 智能体请求 | 交付 | 质量检查 | 耗时（秒） | 工具成功/已判定 |',
        '|---|---|---|---|---|---|---|---|---:|---|']
    for row in rows:
        q, m = row['quality'], row['tools'].get('metrics') or {}
        quality_text = ((('部分检查 ' if q['state'] == 'partial' else
                         '存在未通过项 ' if q['failed_checks'] else '全部符合 ') +
                         f"{q['passed_checks']}/{q['total_checks']}")
                        if q['state'] in {'checked', 'partial'} else '无法评价')
        tool_text = f"{m['succeeded']}/{m['succeeded'] + m['failed']}" if m else '不可用'
        functional_text = FUNCTIONAL_LABELS[row['functional']['state']] if row.get('functional') else '—'
        lines.append(f"| {cell(display_names[row['workspace']])} | {cell(row['case_id'])} {cell(row['title'])} | "
                     f"{cell(TEST_LABELS[row['test_status']])} | {functional_text} | {cell(TASK_LABELS[row['task']['state']])} | "
                     f"{cell(AGENT_LABELS[row['agent']['state']])} | "
                     f"{cell(DELIVERY_LABELS[row['delivery']['state']])} | "
                     f"{cell(quality_text)} | {cell(row.get('task_seconds'))} | {cell(tool_text)} |")
    for suite in suites:
        if suite['status'] == 'empty':
            lines.append(f"| {cell(suite['display_name'])} | 待配置 | empty | — | — | — | — | — | — | — |")
    for suite in suites:
        functional = suite['functional']
        if sum(functional.values()):
            functional_rate = f"{suite['functional_pass_rate']:.1%}" if suite['functional_pass_rate'] is not None else '不可计算'
            lines += ['', f"{cell(suite['display_name'])} 功能验收：通过 {functional['pass']}，不通过 {functional['fail']}，"
                      f"阻塞 {functional['blocked']}，执行错误 {functional['error']}；通过率 {functional_rate}（包含阻塞和执行错误）。"]
            if suite.get('coverage_status') == 'incomplete':
                lines.append('该组功能覆盖尚未完成，当前结果不能作为发布门禁。')
            versions = suite.get('qa_versions')
            if versions:
                for build in versions['builds']:
                    lines.append(f"QA MOI：{cell(build['version'])} / {cell(build['git_commit'])}；"
                                 f"涉及 {cell(', '.join(build['cases']))}。")
                if versions['state'] == 'mixed':
                    lines.append('QA MOI 版本在本组内发生变化，结果不能视为同一版本的验证。')
                elif versions['state'] == 'unknown':
                    lines.append('QA MOI 版本未完整记录，无法确认本组结果属于同一版本。')
    if tools['by_tool']:
        lines += ['', '## 智能体工具调用统计', '', '| 工具 | 调用 | 成功 | 失败 | 跳过 | 不明确 | 成功率 |',
                  '|---|---:|---:|---:|---:|---:|---:|']
        for item in tools['by_tool']:
            item_rate = f"{item['success_rate']:.1%}" if item['success_rate'] is not None else '—'
            lines.append(f"| {cell(item['tool'])} | {item['calls']} | {item['succeeded']} | {item['failed']} | "
                         f"{item['skipped']} | {item['unknown']} | {item_rate} |")
    if report['task_errors']:
        lines += ['', '## 任务和测试框架错误', '']
        for item in report['task_errors']:
            lines.append(f"- {cell(item['stage'])} / {cell(item['reason'])}：{cell(', '.join(item['cases']))}")
    if report['tool_errors']:
        lines += ['', '## 工具错误统计', '']
        for item in report['tool_errors']:
            scope = '智能体' if item['scope'] == 'agent' else '评审'
            category = '预期测试错误' if item.get('expected') else '未标记为预期'
            lines.append(f"- {scope} / {category} / {cell(item['tool'])} / {cell(item['code'])}：{item['records']} 条记录，"
                         f"涉及 {cell(', '.join(item['cases']))}。")
    lines.extend(rag_aggregate_lines(report, cell))
    lines += ['', '单个 case 的报告和 result.json 包含会话 ID、trace ID、质量判词、工具参数和错误；'
              'events.jsonl 保存公开事件（凭据已脱敏）。', '',
              '智能体异常、超时、交付或质量问题不使 job 变红；执行、验收或取证错误会使 job 变红。', '',
              '下载 journey-run-* artifact 获取完整报告与证据；artifact 保留 1 天。']
    markdown = '\n'.join(lines)+'\n'
    (destination/'summary.md').write_text(markdown, encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
            output.write(markdown)
    for row in rows:
        case_source = Path(source) / row['case_id']
        if case_source.is_dir():
            shutil.copytree(case_source, destination / 'cases' / row['case_id'], dirs_exist_ok=True)
    return report


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    finish=sub.add_parser('case');finish.add_argument('case_id');finish.add_argument('--root',default='results')
    security=sub.add_parser('scan');security.add_argument('directory')
    summary=sub.add_parser('aggregate');summary.add_argument('--root',default='downloaded-results');summary.add_argument('--out',default='bundle');summary.add_argument('--workspace',default='all')
    args=parser.parse_args()
    if args.command=='case':
        result=finish_case(args.case_id,args.root)
        markdown=(Path(args.root)/args.case_id/'case-summary.md').read_text(encoding='utf-8')
        print(markdown)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
                output.write(markdown)
        return 0 if result['test_status'] == 'completed' else 1
    if args.command=='scan':
        scan(args.directory, configured_secrets())
        return 0
    report=aggregate(args.root,args.out,args.workspace)
    print(json.dumps({k: report[k] for k in ['total', 'test_errors', 'task', 'agent', 'delivery', 'quality', 'tools']}))
    return 1 if report['test_errors'] else 0


if __name__=='__main__':
    raise SystemExit(main())
