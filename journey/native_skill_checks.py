"""Verify discovery, actual packaged-script execution, and independent SQL effects."""
import copy
import csv
import io
import json
import re
from datetime import datetime


def json_objects(text):
    decoder, offset = json.JSONDecoder(), 0
    result = []
    while offset < len(text):
        match = re.search(r'[\[{]', text[offset:])
        if not match:
            break
        offset += match.start()
        try:
            value, end = decoder.raw_decode(text, offset)
        except ValueError:
            offset += 1
            continue
        if isinstance(value, dict):
            result.append(value)
        offset = end
    return result


def observations(path, task_id, skill_id, skill_name):
    calls, results, scripts = {}, {}, []
    discovered = downloaded = False
    if not path.exists():
        return False, False, []
    for line in path.read_text().splitlines():
        event = json.loads(line)
        payload = event.get('payload')
        if not isinstance(payload, dict) or event.get('task_id') != task_id:
            continue
        key = (payload.get('tool_id'), payload.get('call_id'))
        if event.get('kind') == 'tool_call':
            calls[key] = payload.get('arguments')
        elif event.get('kind') == 'tool_result':
            results[key] = payload
    for (tool, call_id), args in calls.items():
        if not isinstance(args, dict):
            continue
        result = results.get((tool, call_id), {})
        if tool == 'skill' and args.get('skill_name') in {skill_id, skill_name}:
            discovered |= result.get('success') is True
        if tool != 'mcp__moi-tools__bash' or not isinstance(args.get('command'), str):
            continue
        command = args['command']
        wrapper = result.get('output')
        output = wrapper.get('output') if isinstance(wrapper, dict) else None
        ok = (result.get('success') is True and isinstance(output, dict) and
              type(output.get('exit_code')) is int and output['exit_code'] == 0 and output.get('output_truncated') is not True)
        if ok and 'download-skill-files' in command and skill_id in command:
            downloaded = True
        if 'progress.py' in command:
            scripts.append({'call_id': call_id, 'command': command, 'succeeded': bool(ok),
                'truncated': result.get('payload_truncated') is True or result.get('result_truncated') is True,
                'objects': json_objects(str(output.get('stdout', ''))) if ok else [], 'output': output})
    return discovered, downloaded, scripts


def canonical(row):
    def cell(key, value):
        if value is None:
            return None
        if isinstance(value, str) and re.match(r'^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}', value):
            try:
                text = value.removesuffix(' +0000 UTC').replace('T', ' ')
                parsed = datetime.strptime(text, '%Y-%m-%d %H:%M:%S' + ('.%f' if '.' in text else ''))
                if key in {'expected_end', 'hiring_date'} and parsed.time().isoformat() == '00:00:00':
                    return parsed.date().isoformat()
                return parsed.isoformat()
            except ValueError:
                return value
        return str(value)
    return {key: cell(key, value) for key, value in row.items()}


def same_records(left, right, key):
    return (len(left) == len(right) and len({item[key] for item in left}) == len(left) and
            len({item[key] for item in right}) == len(right) and
            {item[key]: canonical(item) for item in left} == {item[key]: canonical(item) for item in right})


def same_main(before, after):
    return (same_records(before['customers'], after['customers'], 'customer_uuid') and
            same_records(before['progress_items'], after['progress_items'], 'item_id'))


def preview(spec, before, after, variables):
    old_ids = {item['change_id'] for item in before['progress_changes']}
    added = [item for item in after['progress_changes'] if item['change_id'] not in old_ids]
    kept = [item for item in after['progress_changes'] if item['change_id'] in old_ids]
    valid = (same_main(before, after) and len(added) == len(spec['expected']) and
             same_records(before['progress_changes'], kept, 'change_id') and
             set(after['tables']) == set(before['tables']) | {item['branch_name'] for item in added})
    captures = {}
    for expected in spec['expected']:
        matches = []
        for item in added:
            if item.get('entity') != expected['entity'] or item.get('action') != expected['action']:
                continue
            try:
                record = json.loads(item['after_json'])
            except (TypeError, ValueError):
                continue
            fields = expected['fields']
            if isinstance(record, dict) and all(key in record and canonical(record)[key] == canonical(fields)[key] for key in fields):
                matches.append((item, record))
        if len(matches) != 1:
            valid = False
            continue
        item, record = matches[0]
        valid &= (item.get('status') == 'pending' and re.fullmatch(r'[a-f0-9]{12}', item.get('change_id', '')) is not None and
                  item.get('branch_name') in after['tables'])
        table, key = ('customers', 'customer_uuid') if expected['entity'] == 'customer' else ('progress_items', 'item_id')
        previous = [row for row in before[table] if row[key] == item['record_id']]
        if expected['action'] == 'update':
            saved = json.loads(item['before_json'])
            allowed = set(fields) | {'updated_at', 'version'}
            if 'status' in fields:
                allowed |= {'resume_status', 'completed_at', 'status_reason'}
            valid &= (len(previous) == 1 and isinstance(saved, dict) and canonical(saved) == canonical(previous[0]) and
                      all(canonical(record).get(k) == canonical(previous[0])[k] for k in previous[0] if k not in allowed) and
                      int(record['version']) == int(previous[0]['version']) + 1)
        else:
            valid &= not previous and json.loads(item['before_json']) is None and str(record.get('version')) == '1'
        captures[expected['capture']] = item['change_id']
    valid &= len(set(captures.values())) == len(spec['expected'])
    if valid:
        variables.update(captures)
    return bool(valid), list(captures.values())


def settle(spec, before, after):
    selected = set(spec['ids'])
    old = {item['change_id']: item for item in before['progress_changes']}
    new = {item['change_id']: item for item in after['progress_changes']}
    if (not selected or not selected.issubset(old) or set(old) != set(new) or
            len(old) != len(before['progress_changes']) or len(new) != len(after['progress_changes'])):
        return False, list(selected)
    expected_main = copy.deepcopy(before)
    valid = set(after['tables']) == set(before['tables']) - {old[key]['branch_name'] for key in selected}
    for change_id, item in old.items():
        if change_id not in selected:
            valid &= item == new[change_id]
            continue
        valid &= item.get('status') == 'pending' and new[change_id].get('status') == spec['status'] and item['branch_name'] not in after['tables']
        valid &= all(item[key] == new[change_id].get(key) for key in item if key not in {'status', 'updated_at'})
        if spec['status'] == 'committed':
            table, key = ('customers', 'customer_uuid') if item['entity'] == 'customer' else ('progress_items', 'item_id')
            expected_main[table] = [row for row in expected_main[table] if row[key] != item['record_id']]
            expected_main[table].append(json.loads(item['after_json']))
    return bool(valid and same_main(expected_main, after)), spec.get('reply_ids', spec['ids'])


def _business_effect(spec, before, after, variables):
    if spec['kind'] == 'preview':
        return preview(spec, before, after, variables)
    if spec['kind'] == 'settle':
        return settle(spec, before, after)
    if spec['kind'] == 'initialize':
        return (set(after['tables']) == {'customers', 'progress_items', 'progress_changes'} and
                not any(after[name] for name in ('customers', 'progress_items', 'progress_changes'))), []
    return before == after, []


def business_effect(spec, before, after, variables):
    try:
        return _business_effect(spec, before, after, variables)
    except (KeyError, TypeError, ValueError):
        return False, []


def confirmation_reply(reply, ids, *, checklist=False):
    if not ids:
        return False
    heading = re.search(r'(?:^|\n)\s*(?:#{1,6}\s*|\*\*)?变更确认', reply)
    lines = all(('确认 ' + item) in reply for item in ids)
    if checklist:
        lines &= all(re.search(r'- \[ \]\s+确认 ' + re.escape(item), reply) for item in ids)
    return bool(heading and lines)


def query_result_matches(scripts, required_rows):
    # Agents may print the complete query envelope or selected row objects.
    # Compare data fields from the successful tool result, never the final answer.
    fields = set.intersection(*(set(row) for row in required_rows))
    for script in scripts:
        if not script['succeeded'] or 'query_progress' not in script['command']:
            continue
        rows = []
        for output in script['objects']:
            if isinstance(output.get('rows'), list) and output.get('truncated') is False:
                rows.extend(output['rows'])
            elif fields.issubset(output):
                rows.append(output)
        if (len(rows) == len(required_rows) and all(sum(isinstance(actual, dict) and
                all(key in actual and canonical(actual)[key] == canonical(expected)[key] for key in expected)
                for actual in rows) == 1 for expected in required_rows)):
            return True
    return False


def csv_matches(content, expected_rows):
    try:
        reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
        rows = list(reader)
        return (reader.fieldnames == ['owner_eid', 'owner_name', 'item_count'] and len(rows) == len(expected_rows) and
                {tuple(row[field] for field in reader.fieldnames) for row in rows} ==
                {tuple(str(row[field]) for field in reader.fieldnames) for row in expected_rows})
    except (UnicodeError, csv.Error, KeyError):
        return False
