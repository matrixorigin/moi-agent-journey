"""Track ordered business stages without turning unmet dependencies into failures."""
from collections import Counter

from .client import Failure
from .evidence import utc_now

LABELS = {'pending': '未开始', 'running': '运行中', 'pass': '通过', 'fail': '不通过',
          'blocked': '证据不足', 'error': '执行错误', 'dependency_blocked': '依赖阻塞', 'not_run': '未执行'}


class NativeSkillStages:
    def __init__(self, execution, definitions):
        self.execution = execution
        sessions = execution.case['sessions']
        if len(sessions) != 1 or not isinstance(definitions, list) or not definitions:
            raise Failure('invalid_native_stages')
        self.session_id = sessions[0]['id']
        self.stages, self.phases, self.check_starts = {}, {}, {}
        for spec in definitions:
            if (not isinstance(spec, dict) or set(spec) != {'id', 'title', 'depends_on'} or
                    not isinstance(spec['id'], str) or not spec['id'] or spec['id'] in self.stages or
                    not isinstance(spec['title'], str) or not spec['title'] or
                    not isinstance(spec['depends_on'], list) or
                    any(not isinstance(dependency, str) for dependency in spec['depends_on']) or
                    len(set(spec['depends_on'])) != len(spec['depends_on']) or
                    any(dependency not in self.stages for dependency in spec['depends_on'])):
                raise Failure('invalid_native_stage_dependencies')
            self.stages[spec['id']] = {**spec, 'status': 'pending', 'can_continue': False, 'turns': []}
        for turn in sessions[0]['turns']:
            stage = self.stages.get(turn.get('stage'))
            phase = turn.get('phase')
            if stage is None or not isinstance(phase, str) or phase in self.phases:
                raise Failure('invalid_native_stage_turn')
            item = {'phase': phase, 'status': 'pending', 'can_continue': False}
            stage['turns'].append(item)
            self.phases[phase] = (stage, item)
        if (any(not stage['turns'] for stage in self.stages.values()) or
                [turn['phase'] for stage in self.stages.values() for turn in stage['turns']] !=
                [turn['phase'] for turn in sessions[0]['turns']]):
            raise Failure('native_stages_must_be_contiguous')
        execution.report['native_skill_stages'] = list(self.stages.values())

    def dependencies(self, stage, item):
        missing = [name for name in stage['depends_on'] if not self.stages[name]['can_continue']]
        previous = stage['turns'][:stage['turns'].index(item)]
        missing.extend(turn['phase'] for turn in previous if not turn['can_continue'])
        return missing

    def summarize(self, stage):
        states = [turn['status'] for turn in stage['turns']]
        stage['can_continue'] = all(turn['can_continue'] for turn in stage['turns'])
        precedence = ('error', 'fail', 'blocked', 'running', 'pending', 'dependency_blocked', 'not_run', 'pass')
        stage['status'] = next(status for status in precedence if status in states)
        stage['completed_turns'] = sum(status in {'pass', 'fail', 'blocked'} for status in states)
        stage['seconds'] = round(sum(turn.get('seconds') or 0 for turn in stage['turns']), 3)

    def admit(self, turn, check_count):
        stage, item = self.phases[turn['phase']]
        missing = self.dependencies(stage, item)
        if missing:
            item.update(status='dependency_blocked', blocked_by=missing, reason='prerequisite_not_verified')
            self.summarize(stage)
            self.execution.checkpoint()
            return False
        item.update(status='running', started_at=utc_now())
        self.check_starts[turn['phase']] = check_count
        self.summarize(stage)
        self.execution.checkpoint()
        return True

    def complete(self, phase, checks, *, ready, incomplete):
        stage, item = self.phases[phase]
        current = checks[self.check_starts[phase]:]
        for check in current:
            check.update(phase=phase, stage_id=stage['id'])
        passed = bool(current) and all(check['passed'] for check in current)
        item.update(status='pass' if passed else 'blocked' if incomplete else 'fail', can_continue=ready,
                    finished_at=utc_now(), passed_checks=sum(c['passed'] for c in current), total_checks=len(current),
                    failed_checks=[c['detail'] for c in current if not c['passed']])
        self.attach_task(item)
        self.summarize(stage)
        self.execution.checkpoint()

    def attach_task(self, item):
        matches = [task for task in self.execution.tasks if task['phase'] == self.session_id + ':' + item['phase']]
        if len(matches) == 1:
            task = matches[0]
            item.update({key: task.get(key) for key in ('task_id', 'trace_id', 'conversation_id', 'seconds')})
            item['task_state'] = task.get('state')

    def finish(self):
        report = self.execution.report
        for stage in self.stages.values():
            for item in stage['turns']:
                if item['status'] == 'running':
                    item.update(status='error' if report.get('status') == 'error' else 'fail',
                                reason='execution_interrupted', finished_at=utc_now())
                elif item['status'] == 'pending':
                    missing = self.dependencies(stage, item)
                    item.update(status='dependency_blocked' if missing else 'not_run',
                                reason='prerequisite_not_verified' if missing else 'execution_stopped', blocked_by=missing)
                self.attach_task(item)
                self.summarize(stage)
        counts = Counter(stage['status'] for stage in self.stages.values())
        report['native_skill_stage_summary'] = {status: counts[status] for status in LABELS if status not in {'pending', 'running'}}
        if counts['blocked'] and not (counts['fail'] or counts['error']):
            report['blocked'] = True
        self.execution.evidence.write('native-stages.json', report['native_skill_stages'])
        self.execution.checkpoint()
