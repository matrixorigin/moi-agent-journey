"""Validate the final structured surface from public, task-bound A2UI events."""
import json

from .client import Failure
from .evidence import utc_now

CATALOG = 'https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json'


def matches_fields(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and matches_fields(actual[key], value)
                                               for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            matches_fields(left, right) for left, right in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


def read_surface(path, task_ids):
    surface, components, valid, count = None, {}, True, 0
    for line in path.read_text().splitlines():
        event = json.loads(line)
        if event.get('task_id') not in task_ids or event.get('kind') != 'a2ui':
            continue
        count += 1
        envelope = event.get('payload')
        if not isinstance(envelope, dict) or envelope.get('version') != 'v0.9':
            valid = False
            continue
        keys = [key for key in ('createSurface', 'updateComponents', 'updateDataModel') if key in envelope]
        if len(keys) != 1 or not isinstance(envelope[keys[0]], dict):
            valid = False
            continue
        operation = envelope[keys[0]]
        if keys[0] == 'createSurface':
            if surface is not None or operation.get('catalogId') != CATALOG or not isinstance(operation.get('surfaceId'), str):
                valid = False
            surface = operation.get('surfaceId')
        elif not surface or operation.get('surfaceId') != surface:
            valid = False
        elif keys[0] == 'updateComponents':
            items = operation.get('components')
            if not isinstance(items, list):
                valid = False
                continue
            seen = set()
            for item in items:
                if (not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] or
                        item['id'] in seen or not isinstance(item.get('component'), str)):
                    valid = False
                    continue
                seen.add(item['id'])
                components[item['id']] = item
    reachable, queue = set(), [('root', frozenset())]
    while queue:
        key, ancestors = queue.pop()
        if key in ancestors:
            valid = False
            continue
        if key in reachable:
            continue
        reachable.add(key)
        node = components.get(key)
        if not node:
            valid = False
            continue
        children = node.get('children', [])
        if not isinstance(children, list) or any(not isinstance(child, str) for child in children):
            valid = False
            continue
        queue.extend((child, ancestors | {key}) for child in children)
        if 'child' in node:
            if not isinstance(node['child'], str):
                valid = False
            else:
                queue.append((node['child'], ancestors | {key}))
    return surface, components, reachable, valid, count


def question_valid(question):
    options = question.get('options')
    return (question.get('type') == 'single' and isinstance(question.get('title'), str) and bool(question['title'].strip()) and
            isinstance(question.get('answerPath'), str) and question['answerPath'].startswith('/answers/') and
            len(question['answerPath'].split('/')) == 3 and bool(question['answerPath'].split('/')[-1]) and
            isinstance(options, list) and len(options) >= 2 and all(isinstance(option, dict) and
                isinstance(option.get('value'), str) and option['value'] and isinstance(option.get('label'), str) and
                option['label'] for option in options) and len({option['value'] for option in options}) == len(options))


def question_action(path, task_id, labels, answer):
    if (not isinstance(labels, list) or any(not isinstance(label, str) for label in labels) or
            len(set(labels)) != len(labels) or answer not in labels):
        raise Failure('invalid_a2ui_answer_case')
    surface, components, reachable, valid, count = read_surface(path, {task_id})
    questions = [components[key] for key in reachable if key in components and components[key].get('component') == 'Question']
    if not valid or not count or len(questions) != 1 or not question_valid(questions[0]):
        return None
    question = questions[0]
    if sorted(option['label'] for option in question['options']) != sorted(labels) or 'result' in question:
        return None
    selected = next(option['value'] for option in question['options'] if option['label'] == answer)
    return {'version': 'v0.9', 'action': {'name': 'submit_answer', 'surfaceId': surface,
        'sourceComponentId': question['id'], 'timestamp': utc_now(),
        'context': {question['answerPath'].split('/')[-1]: selected}}}


def evaluate_a2ui(path, task_id, session_id, expected, *, prior_task_id=None):
    if (not isinstance(expected, dict) or set(expected) != {'component', 'fields'} or
            expected['component'] not in {'Chart', 'Table', 'Question'} or not isinstance(expected['fields'], dict)):
        raise Failure('invalid_a2ui_case')
    surface, components, reachable, valid, count = read_surface(path, {task_id, prior_task_id} - {None})
    root = components.get('root', {})
    matches = [components[key] for key in reachable if key in components and
               components[key].get('component') == expected['component']]
    delivered = bool(surface) and root.get('component') == 'Column' and len(matches) == 1
    content_ok = delivered and matches_fields(matches[0], expected['fields'])
    if content_ok and expected['component'] == 'Question':
        content_ok = question_valid(matches[0])
    checks = [
        {'session': session_id, 'rule': '结构化事件的协议版本和展示面关联正确', 'passed': count > 0 and valid,
         'detail': '' if count > 0 and valid else 'a2ui_envelope_invalid_or_missing'},
        {'session': session_id, 'rule': '根布局可到达指定组件', 'passed': delivered,
         'detail': '' if delivered else 'a2ui_component_missing_or_unreachable'},
        {'session': session_id, 'rule': '结构化组件数据正确', 'passed': bool(content_ok),
         'detail': '' if content_ok else 'a2ui_data_mismatch'},
    ]
    return checks, delivered
