"""Complete synthetic failure -> reviewed release -> separate optimization demo."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.testclient import TestClient
from prompt_optimizer.adapters.failures import (SyntheticMarkerSanitizer, DeterministicFailureAnalyzer,
    InMemoryFailureStore, ExclusiveJsonlDatasetWriter)
from prompt_optimizer.application.failures import ImportFailures, ReviewFailures, ExportReviewedFailures, FailureError
from prompt_optimizer.adapters.jsonl_dataset import JsonlDatasetLoader
from prompt_optimizer.adapters.filesystem_registry import FilesystemPromptRepository
from prompt_optimizer.adapters.agent_datasets import FileDatasetResolver
from prompt_optimizer.adapters.agent_http import HttpBackendConfiguration, HttpOptimizationBackend
from prompt_optimizer.domain import PromptVersion
from prompt_optimizer.http_runtime import OfflineConfiguration, build_offline_app
from prompt_optimizer.interfaces.agent_tool import AgentTools


def synthetic_trace():
    return {'id': 'unknown', 'prompt_name': 'clinic', 'prompt_version': 'v001',
        'timestamp': '2026-10-06T12:00:00+03:00', 'input': 'Цена? [[PRIVATE:MARKER_INPUT]]',
        'context': {'knowledge': {}, 'nested': [{'note': '[[PRIVATE:MARKER_CONTEXT]]'}]},
        'output': {'action': 'answer', 'note': '[[PRIVATE:MARKER_OUTPUT]]'},
        'observations': ['unsupported_answer'], 'metadata': {'api_key': 'MARKER_CREDENTIAL'},
        'synthetic': True}


def main():
    with TemporaryDirectory(prefix='failures-demo-') as directory:
        root = Path(directory)
        releases = root / 'releases'
        releases.mkdir()
        store = InMemoryFailureStore()
        drafts = ImportFailures(SyntheticMarkerSanitizer(synthetic_confirmed=True),
            DeterministicFailureAnalyzer(), store).execute((synthetic_trace(),))
        exporter = ExportReviewedFailures(store, ExclusiveJsonlDatasetWriter(releases))
        try:
            exporter.execute((drafts[0].id,), dataset_id='reviewed', dataset_version='2', destination='validation.jsonl')
        except FailureError as exc:
            assert exc.code == 'unreviewed_draft'
        else:
            raise AssertionError('Pending draft exported')
        ReviewFailures(store).execute(drafts[0].id, revision=1, reviewer_id='local_human',
            reviewed_at=datetime(2026, 10, 6, tzinfo=timezone.utc), decision='approve',
            expected={'action': 'escalate', 'must_not_invent': True},
            sanitization_confirmed=True, expected_confirmed=True)
        for split in ('train', 'validation'):
            exporter.execute((drafts[0].id,), dataset_id='reviewed', dataset_version='2', destination=split + '.jsonl')
        loaded = JsonlDatasetLoader().load(str(releases / 'validation.jsonl'), dataset_id='reviewed', version='2')
        assert loaded.cases[0].id == 'case_unknown'
        repository = FilesystemPromptRepository(root / 'registry')
        repository.bootstrap(PromptVersion('clinic', 'v001', 'Передай оператору', status='production'))
        # build_offline_app explicitly composes FakeOptimizer and FakeLLMClient.
        app = build_offline_app(OfflineConfiguration(str(root / 'registry'), 'Не выдумывай цены. Передай оператору', {
            ('v001', 'case_unknown'): {'action': 'answer'},
            ('v002', 'case_unknown'): {'action': 'escalate'}}))
        with TestClient(app) as http:
            backend = HttpOptimizationBackend(HttpBackendConfiguration('http://testserver'), client=http)
            resolver = FileDatasetResolver({('reviewed', '2'): (str(releases / 'train.jsonl'),
                str(releases / 'validation.jsonl'))}, JsonlDatasetLoader())
            tools = AgentTools(backend, resolver)
            summary = tools.optimize_prompt({'prompt_name': 'clinic', 'production': True,
                'dataset_id': 'reviewed', 'dataset_version': '2', 'candidate_version': 'v002'})
            report = tools.get_optimization_report({'report_id': summary['report']['id']})
            assert report['report']['schema_version'] == '1.0'
            assert repository.read('clinic', 'v002').status.value == 'candidate'
            assert repository.production('clinic').version == 'v001'
        print('Offline reviewed failure release and separate optimization verified.')


if __name__ == '__main__':
    main()
