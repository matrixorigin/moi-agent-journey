"""Exercise real Agent package import/export/default versions on owned objects."""
import hashlib
import io
import time
import zipfile

from .client import Failure, error_record
from .resource_journey import ResourceJourney

MEDIA_TYPE = 'application/vnd.moi.agent+zip'


def package_bytes(files):
    files = {name: value for name, value in files.items() if name != 'checksums.txt'}
    files['checksums.txt'] = ''.join(hashlib.sha256(value).hexdigest() + '  ' + name + '\n'
                                   for name, value in sorted(files.items())).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_STORED) as archive:
        for name, value in sorted(files.items()):
            archive.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), value)
    return output.getvalue()


def package_files(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        items = archive.infolist()
        if (len(items) > 1000 or sum(item.file_size for item in items) > 32_000_000 or
                len({item.filename for item in items}) != len(items) or any(
                    item.filename.startswith('/') or '..' in item.filename.split('/') or '\\' in item.filename
                    for item in items)):
            raise Failure('invalid_export_archive')
        files = {item.filename: archive.read(item) for item in items if not item.is_dir()}
    expected = package_bytes(files)
    with zipfile.ZipFile(io.BytesIO(expected)) as rebuilt:
        if files.get('checksums.txt') != rebuilt.read('checksums.txt'):
            raise Failure('export_checksum_mismatch')
    return files


class AgentPackageJourney(ResourceJourney):
    kind = 'agent'
    modes = {'archive', 'roundtrip', 'invalid'}

    def __init__(self, execution, definition, checks):
        if not isinstance(definition, dict) or set(definition) != {'mode'} or definition['mode'] not in self.modes:
            raise Failure('invalid_package_case')
        self.execution, self.checks, self.mode = execution, checks, definition['mode']
        self.session_id = execution.case['sessions'][0]['id']
        self.workspace = execution.root.rsplit('/', 1)[-1]
        self.agent_id = 'j2-package-' + execution.report['run_id'].replace('-', '')
        self.agent_pending, self.builder = False, None
        self.state = {'mode': self.mode, 'agent_id': self.agent_id, 'cleanup': 'pending'}
        execution.report['package_journey'] = self.state
        self.previous_observer = execution.client.observer
        execution.client.observer = self.observe

    def observe(self, exchange):
        super().observe(exchange)
        if exchange.get('path', '').startswith(self.execution.root + '/agent-packages'):
            self.execution.evidence.append('package-http.jsonl', exchange)

    def load(self, raw, expected_status=None):
        try:
            result = self.execution.client.request('POST', self.execution.root + '/agent-packages/load',
                                                   raw=raw, content_type=MEDIA_TYPE)
        except Failure as error:
            if expected_status is not None and error.code == 'http_error' and error.http_status not in {None, 401, 403}:
                return error.http_status == expected_status
            raise
        if expected_status is not None:
            return False
        if result.get('code') != 0 or not isinstance(result.get('data'), dict):
            raise Failure('invalid_package_load_response')
        return result['data']

    def versions(self):
        return self.request('GET', self.agent_path + '/versions')

    def package_contents(self, files):
        return files

    def prepare(self):
        if not self.absent(self.agent_path):
            raise Failure('package_agent_already_exists')
        old_code = 'OLD-' + self.execution.report['run_id'][:8]
        new_code = 'NEW-' + self.execution.report['run_id'][:8]
        instruction = ('Follow the user instructions and deliver requested files.' if self.mode == 'archive'
                       else 'Always respond only with this code: ' + old_code)
        files = {'agent.yaml': ('schemaVersion: 1\nid: '+self.agent_id+'\nversion: 1.0.0\nname: '+self.agent_id+
            '\nmodel: '+self.execution.model+'\ninstruction:\n  path: prompts/main.md\nmoiResourceRefs:\n'
            '  tools: []\n  skills: []\n  knowledgeBases: []\n  catalogFiles: []\n  channelBindings: []\n').encode(),
            'prompts/main.md': instruction.encode()}
        self.agent_pending = True
        self.execution.checkpoint()
        loaded = self.load(package_bytes(self.package_contents(files)))
        version = loaded.get('version', {})
        self.record('导入包得到本次可运行版本', version.get('agent_id') == self.agent_id and
            version.get('version') == '1.0.0' and version.get('status') == 'runnable', 'package_load_mismatch', required=True)
        agent = self.request('GET', self.agent_path)
        self.record('导入后智能体身份及状态正确', agent.get('id') == self.agent_id and agent.get('name') == self.agent_id and
            agent.get('workspace_id') == self.workspace and agent.get('status') == 'active', 'package_agent_mismatch', required=True)
        initial = self.versions()
        self.record('初始版本列表只包含本次版本', [v.get('version') for v in initial.get('items', [])] == ['1.0.0'],
                    'package_initial_versions_mismatch', required=True)
        if self.mode == 'roundtrip':
            raw = self.execution.client.request('GET', self.execution.root + '/agent-packages/' + self.agent_id +
                                                '/versions/1.0.0/export', binary=True)
            try:
                exported = package_files(raw)
                valid = (exported.get('agent.yaml') == files['agent.yaml'] and
                         exported.get('prompts/main.md') == files['prompts/main.md'])
            except (ValueError, zipfile.BadZipFile, Failure):
                valid, exported = False, {}
            self.record('导出包校验和、身份及指令一致', valid, 'package_export_mismatch', required=True)
            directory = self.execution.evidence.root / 'outputs' / 'package'
            directory.mkdir(parents=True, exist_ok=True)
            (directory/'export.zip').write_bytes(raw)
            exported['agent.yaml'] = exported['agent.yaml'].replace(b'version: 1.0.0\n', b'version: 1.0.1\n')
            exported['prompts/main.md'] = ('Always respond only with this code: ' + new_code).encode()
            newer = self.load(package_bytes(exported)).get('version', {})
            self.record('修改导出包后导入新版本', newer.get('agent_id') == self.agent_id and newer.get('version') == '1.0.1' and
                        newer.get('status') == 'runnable', 'package_reload_mismatch', required=True)
            changed = self.request('POST', self.agent_path + '/versions/1.0.1/default', {})
            self.record('新版本可设为默认', changed.get('agent_id') == self.agent_id and changed.get('default_version') == '1.0.1',
                        'package_default_mismatch', required=True)
            current = self.versions()
            self.record('版本链完整且默认指向新版本', {v.get('version') for v in current.get('items', [])} == {'1.0.0', '1.0.1'} and
                current.get('lineage', {}).get('default_version') == '1.0.1', 'package_version_chain_mismatch')
        elif self.mode == 'invalid':
            conflicting = {**files, 'prompts/main.md': b'Conflicting immutable version contents.'}
            for name, raw, status in [('damaged', b'not a package', 400), ('conflict', package_bytes(conflicting), 400)]:
                self.record('拒绝智能体包 ' + name, self.load(raw, status), 'invalid_package_accepted:' + name)
                self.record('拒绝后版本和默认值不变 ' + name, self.versions() == initial, 'invalid_package_mutated_versions')
        return {'package_code': new_code if self.mode == 'roundtrip' else old_code}

    def cleanup(self, *, dependencies_confirmed):
        client, deadline = self.execution.client, self.execution.client.deadline
        client.deadline = time.monotonic() + 30
        try:
            if not dependencies_confirmed:
                raise Failure('dependent_task_unconfirmed')
            if self.agent_pending and not self.absent(self.agent_path):
                agent = client.product('GET', self.execution.root + self.agent_path)
                if (agent.get('id'), agent.get('name'), agent.get('workspace_id')) != (self.agent_id, self.agent_id, self.workspace):
                    raise Failure('cleanup_package_identity_mismatch')
                client.product('PATCH', self.execution.root + self.agent_path, {'status': 'archived'})
                if client.product('GET', self.execution.root + self.agent_path).get('status') != 'archived':
                    raise Failure('cleanup_package_unconfirmed')
            self.state['cleanup'] = 'completed'
        except Exception as error:
            self.state['cleanup'] = 'incomplete'
            self.execution.report['harness_errors'].append(error_record('cleanup_package', error))
        finally:
            client.deadline = deadline
            client.observer = self.previous_observer
            self.execution.checkpoint()
