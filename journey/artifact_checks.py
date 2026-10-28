"""Check a real downloadable text artifact produced by one Agent task."""
import json
import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .client import Failure


def find_task_artifact(execution, conversation_id, task_id, filename, checks, session_id):
    if not filename or filename in {'.', '..'} or Path(filename).name != filename or '\\' in filename:
        raise Failure('invalid_artifact_assertion')
    execution.collect_artifacts(conversation_id)
    inventory = execution.evidence.root / 'artifacts' / conversation_id / 'inventory.json'
    items = json.loads(inventory.read_text())
    matches = [item for item in items if item.get('task_id') == task_id and item.get('file_name') == filename]
    delivered = len(matches) == 1
    checks.append({'session': session_id, 'rule': '交付指定文件', 'passed': delivered,
                   'detail': '' if delivered else 'matching_artifacts:' + str(len(matches))})
    path = None
    if delivered:
        path = (execution.evidence.root / matches[0]['local_path']).resolve()
        if not path.is_relative_to(execution.evidence.root.resolve()):
            raise Failure('unsafe_artifact_path')
    return path


def check_text_artifact(execution, conversation_id, task_id, filename, expected, checks, session_id):
    if not expected:
        raise Failure('invalid_artifact_assertion')
    path = find_task_artifact(execution, conversation_id, task_id, filename, checks, session_id)
    delivered, content_ok = path is not None, False
    if delivered:
        content = path.read_bytes()
        expected_bytes = expected.encode('utf-8')
        content_ok = content in (expected_bytes, expected_bytes + b'\n', expected_bytes + b'\r\n')
    checks.append({'session': session_id, 'rule': '下载文件内容正确', 'passed': content_ok,
                   'detail': '' if content_ok else 'artifact_content_mismatch' if delivered else 'artifact_unavailable'})
    execution.report['validation'] = {'checks': checks}
    execution.checkpoint()
    return delivered


def xlsx_first_cell(path):
    """Read A1 from the first worksheet without executing workbook content."""
    with zipfile.ZipFile(path) as archive:
        items = archive.infolist()
        if (len(items) > 1000 or sum(item.file_size for item in items) > 20_000_000 or
                len({item.filename for item in items}) != len(items)):
            raise ValueError('invalid_workbook_size_or_members')

        def xml(name):
            data = archive.read(name)
            if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                raise ValueError('unsafe_workbook_xml')
            return ElementTree.fromstring(data)

        workbook = xml('xl/workbook.xml')
        sheets = [node for node in workbook.iter() if node.tag.rsplit('}', 1)[-1] == 'sheet']
        if not sheets:
            raise ValueError('worksheet_missing')
        relation = sheets[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        if not relation:
            raise ValueError('worksheet_relation_missing')
        relationships = xml('xl/_rels/workbook.xml.rels')
        targets = [item.get('Target') for item in relationships if item.get('Id') == relation and
                   item.get('TargetMode') != 'External']
        if len(targets) != 1 or not isinstance(targets[0], str):
            raise ValueError('worksheet_relation_invalid')
        target = targets[0].lstrip('/') if targets[0].startswith('/') else posixpath.join('xl', targets[0])
        target = posixpath.normpath(target)
        if not target.startswith('xl/worksheets/') or '\\' in target:
            raise ValueError('worksheet_path_invalid')
        worksheet = xml(target)
        cells = [node for node in worksheet.iter() if node.tag.rsplit('}', 1)[-1] == 'c' and node.get('r') == 'A1']
        if len(cells) != 1:
            raise ValueError('cell_a1_missing_or_ambiguous')
        cell = cells[0]
        if cell.get('t') == 'inlineStr':
            return ''.join(node.text or '' for node in cell.iter() if node.tag.rsplit('}', 1)[-1] == 't')
        values = [node.text or '' for node in cell if node.tag.rsplit('}', 1)[-1] == 'v']
        if len(values) != 1:
            raise ValueError('cell_a1_value_missing')
        if cell.get('t') == 's':
            strings = [node for node in xml('xl/sharedStrings.xml') if node.tag.rsplit('}', 1)[-1] == 'si']
            index = int(values[0])
            if index < 0 or index >= len(strings):
                raise ValueError('shared_string_index_invalid')
            return ''.join(node.text or '' for node in strings[index].iter() if node.tag.rsplit('}', 1)[-1] == 't')
        return values[0]


def check_xlsx_artifact(execution, conversation_id, task_id, filename, expected, checks, session_id):
    if not expected or not filename.endswith('.xlsx'):
        raise Failure('invalid_artifact_assertion')
    path = find_task_artifact(execution, conversation_id, task_id, filename, checks, session_id)
    delivered, content_ok = path is not None, False
    detail = 'artifact_unavailable'
    if delivered:
        try:
            content_ok = xlsx_first_cell(path) == expected
            detail = '' if content_ok else 'cell_a1_mismatch'
        except (OSError, ValueError, KeyError, RuntimeError, NotImplementedError, zipfile.BadZipFile, ElementTree.ParseError):
            detail = 'invalid_workbook'
    checks.append({'session': session_id, 'rule': '工作簿第一个表的 A1 内容正确',
                   'passed': content_ok, 'detail': detail})
    execution.report['validation'] = {'checks': checks}
    execution.checkpoint()
    return delivered
