from dataclasses import replace, FrozenInstanceError
from datetime import datetime, timezone
import json
import subprocess
import sys
import pytest
from prompt_optimizer.adapters.failures import *
from prompt_optimizer.application.failures import *
from prompt_optimizer.application.reporting import json_value
from prompt_optimizer.adapters.jsonl_dataset import JsonlDatasetLoader
from prompt_optimizer.domain.failures import FailureCluster
from prompt_optimizer.domain import DomainValidationError


def trace():
    return dict(id='f1', prompt_name='clinic', prompt_version='v001', timestamp='2026-10-06T12:00:00+03:00',
        input='Юникод [[PRIVATE:RAW_INPUT]]', context={'nested': [{'note': '[[PRIVATE:RAW_CONTEXT]]', 'password': 'RAW_SECRET'}]},
        output={'note': '[[PRIVATE:RAW_OUTPUT]]'}, observations=['unsupported'], metadata={'token': 'RAW_TOKEN'}, synthetic=True)


def setup_flow():
    store = InMemoryFailureStore()
    importer = ImportFailures(SyntheticMarkerSanitizer(synthetic_confirmed=True), DeterministicFailureAnalyzer(), store)
    return store, importer, importer.execute((trace(),))[0]


def approve(store, draft, **changes):
    args = dict(revision=draft.revision, reviewer_id='human', reviewed_at=datetime.now(timezone.utc),
        decision='approve', expected={'action': 'escalate'}, sanitization_confirmed=True, expected_confirmed=True)
    args.update(changes)
    return ReviewFailures(store).execute(draft.id, **args)


def test_review_export_and_invalidation(tmp_path):
    store, _, draft = setup_flow()
    export = ExportReviewedFailures(store, ExclusiveJsonlDatasetWriter(tmp_path))
    def run():
        return export.execute((draft.id,), dataset_id='reviewed', dataset_version='2', destination='new.jsonl')
    with pytest.raises(FailureError, match='unreviewed_draft'):
        run()
    review = approve(store, draft)
    assert review.case.expected != store.failures['f1'].output
    with pytest.raises(FailureError, match='invalid_review_transition'):
        approve(store, draft)
    dataset = run()
    assert JsonlDatasetLoader().load(str(tmp_path / 'new.jsonl'), dataset_id='reviewed', version='2') == dataset
    assert 'RAW_' not in (tmp_path / 'new.jsonl').read_text(encoding='utf-8')
    with pytest.raises(FailureError, match='export_failure'):
        run()
    revised = ReviewFailures(store).revise(draft.id, input='Изменённый', context={})
    assert revised.revision == 2 and store.history == [review]
    with pytest.raises(FailureError, match='unreviewed_draft'):
        run()
    with pytest.raises(FailureError, match='invalid_review_transition'):
        approve(store, draft)


@pytest.mark.parametrize('changes', [{'extra': 1}, {'synthetic': False}, {'timestamp': '2026-10-06'},
    {'metadata': {'unknown': 'RAW_UNKNOWN'}}, {'context': {'x': float('nan')}}, {'id': ''}, {'output': []}, {'observations': []}])
def test_strict_sanitization_errors(changes):
    store = InMemoryFailureStore()
    importer = ImportFailures(SyntheticMarkerSanitizer(synthetic_confirmed=True), DeterministicFailureAnalyzer(), store)
    with pytest.raises(FailureError) as caught:
        importer.execute(({**trace(), **changes},))
    assert caught.value.__cause__ is not None
    assert 'RAW_' not in str(caught.value) and not store.failures


def test_immutable_unicode_nested_and_safe_artifacts():
    store, _, draft = setup_flow()
    failure = store.failures['f1']
    assert failure.timestamp.utcoffset().total_seconds() == 10800
    assert failure.input == 'Юникод [redacted]'
    assert 'RAW_' not in json.dumps(json_value((failure, draft)))
    assert draft.expected is None
    with pytest.raises(TypeError):
        failure.context['nested'][0]['note'] = 'change'
    with pytest.raises(FrozenInstanceError):
        failure.input = 'change'
    with pytest.raises(DomainValidationError):
        replace(failure, timestamp=datetime.now())


def test_duplicate_import_is_atomic():
    store, importer, _ = setup_flow()
    with pytest.raises(FailureError, match='duplicate_identity'):
        importer.execute((trace(), {**trace(), 'id': 'new'}))
    assert set(store.failures) == {'f1'}
    with pytest.raises(FailureError, match='duplicate_identity'):
        importer.execute((trace(), trace()))


@pytest.mark.parametrize('changes', [{'expected': {}}, {'expected': None}, {'expected_confirmed': False},
    {'sanitization_confirmed': False}, {'reviewer_id': '../private'}, {'reviewed_at': datetime.now()}])
def test_invalid_review(changes):
    store, _, draft = setup_flow()
    with pytest.raises(FailureError, match='invalid_expected'):
        approve(store, draft, **changes)
    assert not store.reviews


def test_rejection_duplicate_export_and_existing(tmp_path):
    store, _, draft = setup_flow()
    approve(store, draft, decision='reject')
    exporter = ExportReviewedFailures(store, ExclusiveJsonlDatasetWriter(tmp_path))
    for ids, code in [((draft.id,), 'unreviewed_draft'), ((draft.id, draft.id), 'duplicate_identity')]:
        with pytest.raises(FailureError, match=code):
            exporter.execute(ids, dataset_id='d', dataset_version='2', destination='new.jsonl')
    assert not list(tmp_path.iterdir())


def test_determinism_and_invalid_ports():
    sanitizer = SyntheticMarkerSanitizer(synthetic_confirmed=True)
    failures = tuple(sanitizer.sanitize({**trace(), 'id': fid}) for fid in ('b', 'a'))
    analyzer = DeterministicFailureAnalyzer()
    assert analyzer.analyze(failures) == analyzer.analyze(failures[::-1])
    assert analyzer.analyze(failures)[0].failure_ids == ('a', 'b')
    class Bad:
        def analyze(self, failures):
            return (FailureCluster('c', ('unknown',), ('tag',), 'observation'),)
    store = InMemoryFailureStore()
    with pytest.raises(FailureError, match='unknown_references'):
        ImportFailures(sanitizer, Bad(), store).execute((trace(),))
    assert not store.failures
    class Invalid:
        def sanitize(self, trace):
            return trace
    with pytest.raises(FailureError, match='invalid_sanitizer_output'):
        ImportFailures(Invalid(), analyzer, store).execute((trace(),))


def test_full_offline_example():
    result = subprocess.run([sys.executable, 'examples/failures_offline.py'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'verified' in result.stdout


def test_import_without_io_and_capabilities():
    script = '''
import builtins, socket
def fail(*args, **kwargs): raise AssertionError('I/O on import')
import pydantic
builtins.open = fail
socket.socket = fail
import prompt_optimizer.domain.failures
import prompt_optimizer.ports.failures
import prompt_optimizer.application.failures
import prompt_optimizer.adapters.failures
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not hasattr(DeterministicFailureAnalyzer(), 'review')
    assert not hasattr(ImportFailures(None, None, None), 'export')
    assert InMemoryFailureStore().drafts == {}


@pytest.mark.parametrize('result', [None, [], (), ('invalid',),
    (FailureCluster('c', ('f1',), ('tag',), 'basis'), FailureCluster('c2', ('f1',), ('tag',), 'basis'))])
def test_invalid_analyzer_results(result):
    class Analyzer:
        def analyze(self, failures):
            return result
    store = InMemoryFailureStore()
    with pytest.raises(FailureError, match='invalid_analyzer_output'):
        ImportFailures(SyntheticMarkerSanitizer(synthetic_confirmed=True), Analyzer(), store).execute((trace(),))
    assert not store.failures and not store.drafts


def test_port_failures_preserve_safe_causes():
    class Bad:
        def sanitize(self, trace):
            raise RuntimeError('RAW_SECRET internal/path')
    with pytest.raises(FailureError) as caught:
        ImportFailures(Bad(), None, None).execute((trace(),))
    assert caught.value.code == 'sanitization_failure'
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert 'RAW_SECRET' not in str(caught.value)
    class BadStore(InMemoryFailureStore):
        def save_import(self, failures, drafts):
            raise OSError('RAW_SECRET')
    with pytest.raises(FailureError, match='storage_failure') as caught:
        ImportFailures(SyntheticMarkerSanitizer(synthetic_confirmed=True), DeterministicFailureAnalyzer(), BadStore()).execute((trace(),))
    assert isinstance(caught.value.__cause__, OSError)


def test_duplicate_existing_release_and_safe_destinations(tmp_path):
    store, _, draft = setup_flow()
    approve(store, draft)
    exporter = ExportReviewedFailures(store, ExclusiveJsonlDatasetWriter(tmp_path))
    existing = exporter.execute((draft.id,), dataset_id='d', dataset_version='1', destination='old.jsonl')
    original = (tmp_path / 'old.jsonl').read_bytes()
    with pytest.raises(FailureError, match='duplicate_identity'):
        exporter.execute((draft.id,), dataset_id='d', dataset_version='2', destination='new.jsonl', existing=existing)
    for destination in ('../source.jsonl', 'registry/prompt.json', str(tmp_path / 'absolute.jsonl')):
        with pytest.raises(FailureError, match='export_failure'):
            exporter.execute((draft.id,), dataset_id='d', dataset_version='2', destination=destination)
    assert (tmp_path / 'old.jsonl').read_bytes() == original
    assert not (tmp_path / 'new.jsonl').exists()


def test_sanitized_key_collision_rejected():
    raw = trace()
    raw['context'] = {'[[PRIVATE:A]]': 1, '[[PRIVATE:B]]': 2}
    with pytest.raises(FailureError, match='sanitization_failure'):
        SyntheticMarkerSanitizer(synthetic_confirmed=True).sanitize(raw)


def test_schema_and_model_validation():
    assert SyntheticTrace.model_json_schema()['additionalProperties'] is False
    failure = SyntheticMarkerSanitizer(synthetic_confirmed=True).sanitize(trace())
    for changes in ({'id': ''}, {'context': {'nested': float('inf')}}, {'observations': 'string'}):
        with pytest.raises(DomainValidationError):
            replace(failure, **changes)
    store, _, draft = setup_flow()
    for changes in ({'revision': True}, {'status': 'approved'}, {'source_failure_ids': ()}, {'expected': {'a': float('nan')}}):
        with pytest.raises(DomainValidationError):
            replace(draft, **changes)
    assert store.get_review(draft.id) is None


def test_long_identity_and_strict_review_revision():
    store = InMemoryFailureStore()
    draft = ImportFailures(SyntheticMarkerSanitizer(synthetic_confirmed=True), DeterministicFailureAnalyzer(), store).execute(({**trace(), 'id': 'a' * 80},))[0]
    assert len(draft.id) <= 80
    with pytest.raises(FailureError, match='invalid_review_transition'):
        approve(store, draft, revision=True)
    with pytest.raises(FailureError, match='invalid_review_transition'):
        approve(store, draft, decision='automatic')
