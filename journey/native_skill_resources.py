"""Shared pinned Skill, owned databases, and identity-bound resource cleanup."""
from .client import Failure, error_record
from .native_skill_cleanup import cleanup_artifacts, cleanup_sandbox
from .native_skill_data import NativeSkillData
from .native_skill_source import import_native_skill
from .owned_skill_journey import OwnedSkillJourney


class NativeSkillResources(OwnedSkillJourney):
    def __init__(self, execution, checks, *, business_database):
        super().__init__(execution, checks)
        suffix = execution.report['run_id'].replace('-', '')
        self.name = '进度管理-j2-' + suffix
        self.temp_dir = '/tmp/j2-native-' + suffix
        self.state.update(name=self.name, temp_dir=self.temp_dir)
        self.data = NativeSkillData(execution, business_database=business_database)

    def prepare_resources(self):
        import_native_skill(self)
        return self.data.setup()

    def cleanup_outputs(self, conversation_id):
        errors = []
        for stage, cleanup in (('cleanup_sandbox', cleanup_sandbox), ('cleanup_artifacts', cleanup_artifacts)):
            try:
                cleanup(self, conversation_id)
            except Exception as error:
                errors.append(error_record(stage, error))
        if errors:
            self.state['output_cleanup_errors'] = errors
            self.execution.checkpoint()
            raise Failure('native_output_cleanup_incomplete', details={'errors': errors})

    def cleanup(self, *, dependencies_confirmed):
        sandbox_records = self.state.get('sandbox_cleanup', {})
        confirmed = dependencies_confirmed and all(item.get('tasks_confirmed') is True for item in sandbox_records.values())
        self.data.cleanup(confirmed)
        super().cleanup(dependencies_confirmed=confirmed)
        if (self.state.get('output_cleanup_errors') or
                any(item.get('cleanup') != 'archived' for item in self.execution.conversations) or any(db['cleanup'] not in {'deleted', 'not_created'} for db in self.data.databases.values()) or
                any(item.get('status') != 'deleted' for item in sandbox_records.values())):
            self.state['cleanup'] = 'incomplete'
            self.execution.checkpoint()
