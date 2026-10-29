"""Use the pinned upstream package without publishing its private source in Git."""
import hashlib
import io
import json
import os
import re
import zipfile
from urllib.parse import urlencode

from .catalog import ROOT
from .client import Failure, segment


class NativeSkillUnavailable(Exception):
    pass


def read_package(raw, definition):
    if hashlib.sha256(raw).hexdigest() != definition['sha256']:
        raise Failure('native_skill_source_checksum_mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if (len(entries) != len(definition['files']) or {item.filename for item in entries} != set(definition['files']) or
                any(item.is_dir() or item.file_size > 1_000_000 or item.flag_bits & 1 or
                    (item.external_attr >> 16) & 0o170000 == 0o120000 for item in entries)):
            raise Failure('native_skill_source_members_mismatch')
        files = {item.filename: archive.read(item) for item in entries}
    if not files['SKILL.md'].startswith(b'---\n'):
        raise Failure('native_skill_manifest_invalid')
    return files


def source_files(execution):
    definition = json.loads((ROOT / 'native-skills.json').read_text())['business-progress']
    file_id = os.environ.get(definition['source_file_variable'], '')
    if not file_id:
        raise NativeSkillUnavailable('native_skill_source_file_not_configured')
    raw = execution.client.request('GET', execution.root + '/files/' + segment(file_id) + '/download',
                                   binary=True, max_bytes=1_000_000)
    files = read_package(raw, definition)
    execution.evidence.write('native-source.json', {**definition, 'file_id': file_id,
        'member_sha256': {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}})
    return files


def with_unique_name(files, name):
    if not re.fullmatch(r'进度管理-j2-[a-f0-9]{32}', name):
        raise Failure('native_skill_test_name_invalid')
    manifest = files['SKILL.md'].decode('utf-8')
    before, header, instruction = manifest.split('---', 2)
    if before or header.count('\nname: 进度管理\n') != 1:
        raise Failure('native_skill_name_contract_changed')
    header = header.replace('\nname: 进度管理\n', '\nname: ' + name + '\n')
    return {**files, 'SKILL.md': ('---' + header + '---' + instruction).encode()}, instruction.strip()


def import_native_skill(journey):
    files = source_files(journey.execution)
    listing = journey.request('GET', '/skills?' + urlencode({'query': journey.name, 'catalog': 'workspace'}))
    if listing.get('total') != 0 or listing.get('items') != []:
        raise Failure('native_skill_name_not_free')
    files, instruction = with_unique_name(files, journey.name)
    journey.import_files(files, instruction)
