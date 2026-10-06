"""Immutable sanitized failure and review contracts. Observations are not causes."""
from dataclasses import dataclass
from datetime import datetime
import re
from collections.abc import Mapping
from typing import Any
from .models import EvaluationCase, _text, _mapping, _strings
from .exceptions import DomainValidationError


def identity(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise DomainValidationError("Expected safe opaque identity")


def aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError("Expected aware timestamp")


@dataclass(frozen=True)
class FailureCase:
    id: str
    prompt_name: str
    prompt_version: str
    timestamp: datetime
    input: str
    context: Mapping[str, Any]
    output: Mapping[str, Any]
    observations: tuple[str, ...]
    sanitization_policy: str

    def __post_init__(self) -> None:
        for name in ('id', 'prompt_name', 'prompt_version', 'sanitization_policy'):
            identity(getattr(self, name))
        aware(self.timestamp)
        _text(self.input, 'input')
        for name in ('context', 'output'):
            object.__setattr__(self, name, _mapping(getattr(self, name), name))
        observations = _strings(self.observations, 'observations')
        if not observations:
            raise DomainValidationError('Observations required')
        object.__setattr__(self, 'observations', observations)


@dataclass(frozen=True)
class FailureCluster:
    id: str
    failure_ids: tuple[str, ...]
    tags: tuple[str, ...]
    basis: str

    def __post_init__(self) -> None:
        identity(self.id)
        for name in ('failure_ids', 'tags'):
            values = _strings(getattr(self, name), name)
            if not values or len(set(values)) != len(values):
                raise DomainValidationError('Nonempty unique references required')
            object.__setattr__(self, name, values)
        for value in self.failure_ids:
            identity(value)
        _text(self.basis, 'basis')


@dataclass(frozen=True)
class FailureDraft:
    id: str
    revision: int
    cluster_id: str
    source_failure_ids: tuple[str, ...]
    input: str
    context: Mapping[str, Any]
    tags: tuple[str, ...]
    explanation: str
    expected: Mapping[str, Any] | None = None
    status: str = 'pending_review'
    review_requirements: tuple[str, ...] = ('confirm_sanitization', 'supply_ground_truth', 'verify_context')

    def __post_init__(self) -> None:
        identity(self.id)
        identity(self.cluster_id)
        if type(self.revision) is not int or self.revision < 1 or self.status != 'pending_review':
            raise DomainValidationError('Invalid draft revision/status')
        for name in ('input', 'explanation'):
            _text(getattr(self, name), name)
        for name in ('source_failure_ids', 'tags', 'review_requirements'):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        if not self.source_failure_ids or len(set(self.source_failure_ids)) != len(self.source_failure_ids):
            raise DomainValidationError('Invalid source references')
        for value in self.source_failure_ids:
            identity(value)
        object.__setattr__(self, 'context', _mapping(self.context, 'context'))
        if self.expected is not None:
            object.__setattr__(self, 'expected', _mapping(self.expected, 'expected'))


@dataclass(frozen=True)
class HumanReview:
    draft: FailureDraft
    reviewer_id: str
    reviewed_at: datetime
    decision: str
    sanitization_confirmed: bool
    expected_confirmed: bool
    case: EvaluationCase | None

    def __post_init__(self) -> None:
        if not isinstance(self.draft, FailureDraft):
            raise DomainValidationError('Invalid draft')
        identity(self.reviewer_id)
        aware(self.reviewed_at)
        if self.decision not in ('approve', 'reject'):
            raise DomainValidationError('Invalid decision')
        if type(self.sanitization_confirmed) is not bool or type(self.expected_confirmed) is not bool:
            raise DomainValidationError('Invalid confirmations')
        if self.decision == 'approve':
            if not self.sanitization_confirmed or not self.expected_confirmed or not isinstance(self.case, EvaluationCase) or not self.case.expected:
                raise DomainValidationError('Approval requires confirmed ground truth')
            if (self.case.id, self.case.input, self.case.context, self.case.tags) != (self.draft.id, self.draft.input, self.draft.context, self.draft.tags):
                raise DomainValidationError('Review content mismatch')
        elif self.case is not None:
            raise DomainValidationError('Rejected review cannot contain trusted case')
