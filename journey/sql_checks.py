"""Verify an observed workspace SQL command and its structured result."""
import json
import re
import shlex

from .shell_output import split_reported_exit, strip_exit_echo


def is_output_limit_tail(parts):
    """Accept a stderr merge and an optional, numeric head limit only."""
    if parts and parts[0] == '2>&1':
        parts = parts[1:]
    if not parts:
        return True
    if parts[:2] != ['|', 'head']:
        return False
    args = parts[2:]
    return (len(args) == 1 and bool(re.fullmatch(r'-[1-9]\d*', args[0]))) or (
        len(args) == 2 and args[0] == '-n' and bool(re.fullmatch(r'[1-9]\d*', args[1])))


def strip_directory_setup(parts):
    """Accept only literal cd commands before one SQL invocation."""
    if '&&' in parts:
        boundary = parts.index('&&')
        prefix, remaining = parts[:boundary], parts[boundary + 1:]
        if (len(prefix) not in {2, 3} or prefix[0] != 'cd' or
                not re.fullmatch(r'[A-Za-z0-9_./-]+', prefix[1]) or
                (len(prefix) == 3 and prefix[2] != '2>/dev/null') or not remaining or '&&' in remaining):
            return None
        return remaining
    if ';' not in parts:
        return parts
    if parts.count(';') != 1:
        return None
    end = parts.index(';')
    prefix, remaining = parts[:end], parts[end + 1:]
    if not prefix or not remaining:
        return None
    while prefix:
        try:
            boundary = prefix.index('||')
        except ValueError:
            boundary = len(prefix)
        command = prefix[:boundary]
        if (len(command) not in {2, 3} or command[0] != 'cd' or
                not re.fullmatch(r'[A-Za-z0-9_./-]+', command[1]) or
                (len(command) == 3 and command[2] != '2>/dev/null')):
            return None
        if boundary == len(prefix):
            break
        prefix = prefix[boundary + 1:]
        if not prefix:
            return None
    return remaining


def cli_query(command):
    """Recognize a CLI invocation with optional directory setup."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';')
        lexer.whitespace_split = True
        lexer.commenters = ''
        parts, capture_exit = strip_exit_echo(list(lexer))
        parts = strip_directory_setup(parts)
    except ValueError:
        return 'unverifiable', None, False
    if parts is None:
        return 'unverifiable', None, False
    if parts[:4] == ['moi-cli', '-o', 'sql', 'run']:
        parts = parts[4:]
    elif parts[:3] == ['moi-cli', 'sql', 'run']:
        parts = parts[3:]
    else:
        return 'unverifiable', None, False
    query, query_file, index = None, False, 0
    while index < len(parts):
        item = parts[index]
        if parts[index] in {'2>&1', '|'} and is_output_limit_tail(parts[index:]):
            break
        if item in {'--db', '--limit'}:
            if index + 1 >= len(parts):
                return 'unverifiable', None, False
            index += 2
            continue
        if item not in {'--query', '--query-file'} or query is not None or query_file or index + 1 >= len(parts):
            return 'unverifiable', None, False
        value = parts[index + 1]
        index += 2
        if item == '--query':
            query = value
        elif value == '<(printf':
            if index < len(parts) and parts[index] == '%s':
                index += 1
            if index >= len(parts) or not parts[index].endswith(')'):
                return 'unverifiable', None, False
            query = parts[index][:-1]
            index += 1
        else:
            query_file = True
    return (('query', query, capture_exit) if query is not None else
            ('file', None, capture_exit) if query_file else ('unverifiable', None, False))


def cli_output(output, capture_exit):
    stdout = output.get('stdout')
    if not isinstance(stdout, str):
        return None, None
    if not capture_exit:
        return stdout, output.get('exit_code')
    return split_reported_exit(stdout, capture_exit)


def unknown_column_error(text, marker):
    if not isinstance(text, str):
        return False
    name = r'\b' + re.escape(marker) + r'\b'
    return any(re.search(pattern, text, re.I) for pattern in (
        r'\bunknown\s+column\b[^\n]*?' + name,
        r'\bno\s+such\s+column\b[^\n]*?' + name,
        r'\b(?:column|field)\b[^\n]*?' + name + r'[^\n]*?\b(?:does\s+not\s+exist|not\s+found|unknown|missing)\b',
    ))


def collect_sql_calls(path, task_id, expected_query, workspace_id):
    calls, matching_calls, results, file_query, unknown_command = set(), {}, {}, False, False
    query = re.sub(r'\s+', '', expected_query).rstrip(';').casefold()
    for line in path.read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        payload = event.get('payload')
        if event.get('task_id') != task_id or not isinstance(payload, dict):
            continue
        call_id = payload.get('call_id')
        if not isinstance(call_id, str) or not call_id:
            continue
        if event.get('kind') == 'tool_call' and payload.get('tool_id') == 'mcp__moi-tools__bash':
            args = payload.get('arguments')
            command = args.get('command', '') if isinstance(args, dict) else ''
            if isinstance(command, str) and 'moi-cli' in command and re.search(r'\bsql\s+run\b', command):
                calls.add(call_id)
                kind, actual_query, capture_exit = cli_query(command)
                if kind == 'query' and re.sub(r'\s+', '', actual_query).rstrip(';').casefold() == query:
                    matching_calls[call_id] = ('cli', capture_exit)
                elif kind == 'file':
                    file_query = True
                elif kind == 'unverifiable':
                    unknown_command = True
        elif event.get('kind') == 'tool_call' and payload.get('tool_id') == 'query_workspace_sql':
            calls.add(call_id)
            args = payload.get('arguments')
            actual_query = args.get('sql') if isinstance(args, dict) else None
            if (isinstance(actual_query, str) and re.sub(r'\s+', '', actual_query).rstrip(';').casefold() == query and
                    args.get('workspace_id') == workspace_id):
                matching_calls[call_id] = ('direct', False)
        elif event.get('kind') == 'tool_result':
            results[call_id] = payload
    return calls, matching_calls, results, file_query, unknown_command


def evaluate_sql(path, task_id, session_id, expected_query, expected_column, expected_value, workspace_id):
    if not all(isinstance(item, str) and item for item in (expected_query, expected_column, expected_value, workspace_id)):
        raise ValueError('invalid_sql_assertion')
    calls, matching_calls, results, file_query, unknown_command = collect_sql_calls(
        path, task_id, expected_query, workspace_id)
    query = re.sub(r'\s+', '', expected_query).rstrip(';').casefold()
    passed, unknown = False, False
    for call_id, (kind, capture_exit) in matching_calls.items():
        payload = results.get(call_id)
        if not payload:
            unknown = True
            continue
        if payload.get('success') is False:
            continue
        if payload.get('success') is not True:
            unknown = True
            continue
        wrapper = payload.get('output')
        output = wrapper.get('output') if isinstance(wrapper, dict) else None
        if not isinstance(output, dict):
            unknown = True
            continue
        try:
            if kind == 'cli':
                if type(output.get('exit_code')) is not int:
                    unknown = True
                    continue
                stdout, cli_status = cli_output(output, capture_exit)
                if cli_status is None:
                    unknown = True
                    continue
                if output['exit_code'] != 0 or cli_status != 0:
                    continue
                result = json.loads(stdout)
                columns, rows = result['columns'], result['result']
                matches = [index for index, column in enumerate(columns) if isinstance(column, dict) and column.get('name') == expected_column]
                if (result.get('status') == 'success' and isinstance(result.get('query_id'), str) and result['query_id'] and
                        result.get('truncated') is not True and len(matches) == 1 and len(rows) == 1 and isinstance(rows[0], list)):
                    cell = rows[0][matches[0]]
                    passed |= isinstance(cell, dict) and cell.get('Valid') is True and str(cell.get('String')) == expected_value
            else:
                columns, rows = output['columns'], output['rows']
                matches = [index for index, column in enumerate(columns) if column == expected_column]
                if (output.get('workspace_id') == workspace_id and
                        isinstance(output.get('sql'), str) and re.sub(r'\s+', '', output['sql']).rstrip(';').casefold() == query and
                        output.get('row_count') == 1 and output.get('truncated') is not True and
                        len(matches) == 1 and len(rows) == 1 and isinstance(rows[0], list)):
                    passed |= str(rows[0][matches[0]]) == expected_value
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            unknown = True
    blocked = ('sql_query_file_unverifiable' if calls and not matching_calls and file_query else
               'sql_command_unverifiable' if calls and not matching_calls and unknown_command else
               'sql_result_unverifiable' if matching_calls and not passed and unknown else None)
    call_detail = '' if matching_calls else (blocked or ('sql_query_mismatch' if calls else 'sql_call_missing'))
    return [
        {'session': session_id, 'rule': '实际执行指定工作区 SQL', 'passed': bool(matching_calls),
         'detail': call_detail},
        {'session': session_id, 'rule': 'SQL 查询记录与结果正确', 'passed': passed,
         'detail': '' if passed else blocked or 'sql_result_not_successful'},
    ], blocked


def evaluate_sql_failure(path, task_id, session_id, expected_query, error_marker, workspace_id, *, error_kind="column"):
    """Require a real workspace SQL error for the requested invalid statement."""
    if not all(isinstance(item, str) and item for item in (expected_query, error_marker, workspace_id)):
        raise ValueError('invalid_sql_failure_assertion')
    if error_kind not in {'column', 'syntax'}:
        raise ValueError('invalid_sql_error_kind')
    def matches_error(text, marker):
        if error_kind == 'column':
            return unknown_column_error(text, marker)
        return isinstance(text, str) and marker.casefold() in text.casefold() and bool(re.search(
            r'syntax\s+error|parse\s+error|SQL\s+syntax|parse\s+sql', text, re.I))
    calls, matching, results, file_query, unknown_command = collect_sql_calls(path, task_id, expected_query, workspace_id)
    unknown, verified = False, None
    for call_id, (kind, capture_exit) in matching.items():
        payload = results.get(call_id)
        if not isinstance(payload, dict):
            unknown = True
            continue
        wrapper = payload.get('output')
        output = wrapper.get('output') if isinstance(wrapper, dict) else None
        if kind == 'cli':
            if not isinstance(output, dict) or type(output.get('exit_code')) is not int:
                unknown = True
                continue
            stdout, cli_status = cli_output(output, capture_exit)
            if cli_status is None:
                unknown = True
                continue
            stderr = output.get('stderr')
            error_text = next((text for text in (stderr, stdout)
                               if matches_error(text, error_marker)), None)
            if cli_status != 0 and error_text is not None:
                verified = []
                if output['exit_code'] != 0:
                    verified.append({'task_id': task_id, 'call_id': call_id, 'kind': 'command_exit',
                                     'code': str(output['exit_code'])})
                elif capture_exit:
                    verified.append({'task_id': task_id, 'call_id': call_id, 'kind': 'captured_command_exit',
                                     'code': str(cli_status)})
                    try:
                        # The diagnostic collector records HTTP errors from JSON lines.
                        lines = error_text.strip().splitlines()
                        error = json.loads(lines[0]) if len(lines) == 1 else None
                    except ValueError:
                        error = None
                    if (isinstance(error, dict) and type(error.get('status')) is int and error['status'] >= 400 and
                            isinstance(error.get('code'), str) and error['code'] and
                            matches_error(error.get('message'), error_marker)):
                        verified.append({'task_id': task_id, 'call_id': call_id, 'kind': 'reported_http_error',
                                         'code': error['code']})
                break
            if output.get('output_truncated') is True or (not isinstance(stderr, str) and not isinstance(stdout, str)):
                unknown = True
        else:
            error = payload.get('error')
            detail = (wrapper.get('error') or wrapper.get('message')) if isinstance(wrapper, dict) else wrapper
            if payload.get('success') is False and matches_error(detail or error, error_marker):
                verified = ([{'task_id': task_id, 'call_id': call_id, 'kind': 'tool_failure', 'code': error}] if (
                    isinstance(error, str) and error) else [])
                break
            if payload.get('success') is not False or not (detail or error):
                unknown = True
    blocked = ('sql_query_file_unverifiable' if calls and not matching and file_query else
               'sql_command_unverifiable' if calls and not matching and unknown_command else
               'sql_failure_unverifiable' if matching and verified is None and unknown else None)
    check = {'session': session_id, 'rule': '工作区 SQL 返回指定'+ ('语法错误' if error_kind == 'syntax' else '字段错误'), 'passed': verified is not None,
             'detail': '' if verified is not None else blocked or 'expected_sql_error_missing'}
    if verified:
        check['expected_tool_errors'] = verified
    return [check], blocked
