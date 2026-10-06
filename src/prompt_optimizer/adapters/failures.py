"""Offline synthetic-only sanitization, deterministic analysis and local storage."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from datetime import datetime
from pydantic import BaseModel, ConfigDict, JsonValue, Field
from prompt_optimizer.domain.failures import FailureCase, FailureCluster, FailureDraft, HumanReview
from prompt_optimizer.domain import Dataset
from prompt_optimizer.application.failures import FailureError
from prompt_optimizer.application.reporting import json_value


class SyntheticTrace(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    id: str
    prompt_name: str
    prompt_version: str
    timestamp: str
    input: str
    context: dict[str, JsonValue]
    output: dict[str, JsonValue]
    observations: list[str] = Field(min_length=1)
    metadata: dict[str, JsonValue]
    synthetic: bool


class SyntheticMarkerSanitizer:
    """Not a production privacy filter. Explicit synthetic attestation required.

    Removes credential fields recursively, replaces artificial [[PRIVATE:...]]
    markers everywhere. Metadata must be empty after credential removal; other
    metadata is rejected rather than silently dropped. No raw values in reports.
    """
    def __init__(self, *, synthetic_confirmed: bool) -> None:
        if synthetic_confirmed is not True:
            raise FailureError('sanitization_failure')

    def sanitize(self, trace: object) -> FailureCase:
        def clean(value):
            if isinstance(value, dict):
                result = {}
                for key, item in value.items():
                    if any(word in key.lower().replace('-', '_') for word in
                           ('secret', 'password', 'credential', 'token', 'authorization', 'api_key', 'cookie')):
                        continue
                    cleaned_key = clean(key)
                    if cleaned_key in result:
                        raise ValueError('Sanitized key collision')
                    result[cleaned_key] = clean(item)
                return result
            if isinstance(value, list):
                return [clean(v) for v in value]
            if isinstance(value, str):
                return re.sub(r'\[\[PRIVATE:[^\]]*\]\]', '[redacted]', value)
            return value
        try:
            raw = SyntheticTrace.model_validate(trace)
            if raw.synthetic is not True:
                raise ValueError('Synthetic confirmation required')
            sanitized = clean(raw.model_dump())
            if sanitized['metadata']:
                raise ValueError('Unsupported metadata')
            # Validate finite JSON, including nested data, before returning.
            json_value(sanitized)
            return FailureCase(sanitized['id'], sanitized['prompt_name'], sanitized['prompt_version'],
                datetime.fromisoformat(sanitized['timestamp']), sanitized['input'], sanitized['context'],
                sanitized['output'], tuple(sanitized['observations']), 'synthetic_markers_v1')
        except Exception as exc:
            raise FailureError('sanitization_failure') from exc


class DeterministicFailureAnalyzer:
    """Sorted observation sets group failures; SHA256 of canonical key gives ID."""
    def analyze(self, failures: tuple[FailureCase, ...]) -> tuple[FailureCluster, ...]:
        groups = {}
        for failure in failures:
            key = tuple(sorted(set(failure.observations)))
            groups.setdefault(key, []).append(failure.id)
        return tuple(FailureCluster('cluster_' + hashlib.sha256(
            json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:24],
            tuple(sorted(ids)), ('observed_failure',),
            'Heuristic grouping by equal observed labels; no root cause established.')
            for key, ids in sorted(groups.items()))


class InMemoryFailureStore:
    """Single-process, no concurrent writers. Batch import commits only after validation."""
    def __init__(self) -> None:
        self.failures, self.drafts, self.reviews, self.history = {}, {}, {}, []

    def save_import(self, failures: tuple[FailureCase, ...], drafts: tuple[FailureDraft, ...]) -> None:
        if any(f.id in self.failures for f in failures) or any(d.id in self.drafts for d in drafts):
            raise FailureError('duplicate_identity')
        self.failures.update({f.id: f for f in failures})
        self.drafts.update({d.id: d for d in drafts})

    def get_draft(self, draft_id: str) -> FailureDraft:
        if draft_id not in self.drafts:
            raise FailureError('unknown_references')
        return self.drafts[draft_id]

    def get_review(self, draft_id: str) -> HumanReview | None:
        return self.reviews.get(draft_id)

    def save_review(self, review: HumanReview) -> None:
        if self.get_draft(review.draft.id) != review.draft or review.draft.id in self.reviews:
            raise FailureError('invalid_review_transition')
        self.reviews[review.draft.id] = review
        self.history.append(review)

    def revise(self, draft: FailureDraft) -> None:
        old = self.get_draft(draft.id)
        if draft.revision != old.revision + 1:
            raise FailureError('invalid_review_transition')
        self.drafts[draft.id] = draft
        self.reviews.pop(draft.id, None)


class ExclusiveJsonlDatasetWriter:
    """Dedicated release root; only a new simple filename allowed, never overwrite.

    Stage/fsync in same directory, then atomic hard-link create (no replacement).
    Unsupported hard links fail closed. Cleanup failure after commit is reported;
    inspect destination before retry. Parent/root must be provisioned by host.
    """
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def write(self, dataset: Dataset, destination: str) -> None:
        if not isinstance(destination, str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.jsonl', destination):
            raise FailureError('export_failure')
        content = ''.join(json.dumps(json_value(case), ensure_ascii=False, allow_nan=False) + '\n'
                          for case in dataset.cases)
        temporary = None
        try:
            if not self.root.is_dir() or self.root.is_symlink():
                raise ValueError('Dedicated root required')
            with tempfile.NamedTemporaryFile(dir=self.root, delete=False, mode='w', encoding='utf-8') as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, self.root / destination)
        except Exception as exc:
            raise FailureError('export_failure') from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError as exc:
                    raise FailureError('export_failure') from exc
