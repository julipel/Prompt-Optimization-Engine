"""Explicit import, local human review, revision and dataset inclusion operations."""
from dataclasses import replace
from datetime import datetime
from collections.abc import Mapping, Callable
from typing import Any, TypeVar
import hashlib
from prompt_optimizer.domain import Dataset, EvaluationCase
from prompt_optimizer.domain.failures import FailureCase, FailureCluster, FailureDraft, HumanReview, identity
from prompt_optimizer.ports.failures import FailureSanitizer, FailureAnalyzer, FailureStore, ReviewedDatasetWriter


T = TypeVar('T')


class FailureError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f'Failure workflow: {code}')


def guarded(code: str, operation: Callable[[], T]) -> T:
    try:
        return operation()
    except FailureError:
        raise
    except Exception as exc:
        raise FailureError(code) from exc


class ImportFailures:
    def __init__(self, sanitizer: FailureSanitizer, analyzer: FailureAnalyzer, store: FailureStore) -> None:
        self.sanitizer, self.analyzer, self.store = sanitizer, analyzer, store

    def execute(self, traces: tuple[object, ...]) -> tuple[FailureDraft, ...]:
        if not isinstance(traces, (tuple, list)) or not traces:
            raise FailureError('import_validation')
        failures = []
        for trace in traces:
            failure = guarded('sanitization_failure', lambda: self.sanitizer.sanitize(trace))
            if type(failure) is not FailureCase:
                raise FailureError('invalid_sanitizer_output')
            guarded('invalid_sanitizer_output', lambda: replace(failure))
            failures.append(failure)
        ids = {f.id for f in failures}
        if len(ids) != len(failures):
            raise FailureError('duplicate_identity')
        clusters = guarded('invalid_analyzer_output', lambda: self.analyzer.analyze(tuple(failures)))
        if not isinstance(clusters, tuple) or not clusters or any(type(c) is not FailureCluster for c in clusters):
            raise FailureError('invalid_analyzer_output')
        references = []
        for cluster in clusters:
            guarded('invalid_analyzer_output', lambda: replace(cluster))
            references.extend(cluster.failure_ids)
        if set(references) - ids:
            raise FailureError('unknown_references')
        if set(references) != ids or len(references) != len(ids) or len({c.id for c in clusters}) != len(clusters):
            raise FailureError('invalid_analyzer_output')
        by_id = {f.id: f for f in failures}
        def draft_id(fid: str) -> str:
            return 'case_' + (fid if len(fid) <= 75 else hashlib.sha256(fid.encode()).hexdigest())
        drafts = guarded('invalid_analyzer_output', lambda: tuple(FailureDraft(draft_id(fid), 1, c.id, (fid,), by_id[fid].input,
            by_id[fid].context, c.tags, c.basis) for c in clusters for fid in c.failure_ids))
        guarded('storage_failure', lambda: self.store.save_import(tuple(failures), drafts))
        return drafts


class ReviewFailures:
    """Trusted local human boundary. Caller identity is not authentication."""
    def __init__(self, store: FailureStore) -> None:
        self.store = store

    def execute(self, draft_id: str, *, revision: int, reviewer_id: str, reviewed_at: datetime,
                decision: str, expected: Mapping[str, Any] | None = None, sanitization_confirmed: bool = False,
                expected_confirmed: bool = False) -> HumanReview:
        draft = guarded('storage_failure', lambda: self.store.get_draft(draft_id))
        previous = guarded('storage_failure', lambda: self.store.get_review(draft_id))
        if type(revision) is not int or draft.revision != revision or previous is not None or decision not in ('approve', 'reject'):
            raise FailureError('invalid_review_transition')
        def build():
            case = EvaluationCase(draft.id, draft.input, expected, draft.context, draft.tags) if decision == 'approve' else None
            return HumanReview(draft, reviewer_id, reviewed_at, decision, sanitization_confirmed, expected_confirmed, case)
        review = guarded('invalid_expected', build)
        guarded('storage_failure', lambda: self.store.save_review(review))
        return review

    def revise(self, draft_id: str, *, input: str, context: Mapping[str, Any],
               expected: Mapping[str, Any] | None = None) -> FailureDraft:
        old = guarded('storage_failure', lambda: self.store.get_draft(draft_id))
        new = guarded('import_validation', lambda: replace(old, revision=old.revision + 1,
            input=input, context=context, expected=expected))
        guarded('storage_failure', lambda: self.store.revise(new))
        return new


class ExportReviewedFailures:
    def __init__(self, store: FailureStore, writer: ReviewedDatasetWriter) -> None:
        self.store, self.writer = store, writer

    def execute(self, draft_ids: tuple[str, ...], *, dataset_id: str, dataset_version: str,
                destination: str, existing: Dataset | None = None) -> Dataset:
        if not isinstance(draft_ids, (tuple, list)) or not draft_ids:
            raise FailureError('import_validation')
        for draft_id in draft_ids:
            guarded('import_validation', lambda: identity(draft_id))
        if existing is not None and not isinstance(existing, Dataset):
            raise FailureError('import_validation')
        if len(set(draft_ids)) != len(draft_ids):
            raise FailureError('duplicate_identity')
        cases = list(existing.cases) if existing is not None else []
        if existing is not None and (existing.id != dataset_id or existing.version == dataset_version):
            raise FailureError('import_validation')
        for draft_id in draft_ids:
            draft = guarded('storage_failure', lambda: self.store.get_draft(draft_id))
            review = guarded('storage_failure', lambda: self.store.get_review(draft_id))
            if review is None or review.decision != 'approve' or review.draft != draft:
                raise FailureError('unreviewed_draft')
            guarded('invalid_expected', lambda: replace(review))
            cases.append(review.case)
        if len({c.id for c in cases}) != len(cases):
            raise FailureError('duplicate_identity')
        dataset = guarded('import_validation', lambda: Dataset(dataset_id, dataset_version, tuple(cases)))
        guarded('export_failure', lambda: self.writer.write(dataset, destination))
        return dataset
