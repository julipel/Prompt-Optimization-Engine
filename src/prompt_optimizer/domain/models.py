"""Validated immutable domain contracts.

Identifiers are opaque nonempty strings. Scores are normalized to [0, 1].
JSON mappings are recursively copied and frozen to prevent caller mutation.
Status changes are represented by a new model, never by mutating a version.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Any

from .exceptions import DomainValidationError


class PromptStatus(str, Enum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    PRODUCTION = "production"
    ARCHIVED = "archived"


class Recommendation(str, Enum):
    """Evaluation advice only; APPROVE does not authorize promotion."""

    APPROVE = "approve"
    REJECT = "reject"
    REVIEW = "review"


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{name} must be a nonempty string")


def _score(value: object, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or not 0 <= value <= 1
    ):
        raise DomainValidationError(f"{name} must be a finite number in [0, 1]")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise DomainValidationError("JSON mapping keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    raise DomainValidationError("Data must contain only finite JSON values")


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{name} must be a mapping")
    return _freeze(value)


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise DomainValidationError(f"{name} must be a sequence of strings")
    for item in value:
        _text(item, name)
    return tuple(value)


def _enum(value: object, enum_type: type[Enum], name: str) -> Enum:
    try:
        return enum_type(value)
    except (ValueError, TypeError) as exc:
        raise DomainValidationError(f"Invalid {name}: {value!r}") from exc


@dataclass(frozen=True)
class PromptVersion:
    """Prompt identity, content, lifecycle status and provenance metadata."""

    name: str
    version: str
    text: str
    status: PromptStatus = PromptStatus.CANDIDATE
    parent_version: str | None = None
    optimizer: str | None = None
    dataset_id: str | None = None
    dataset_version: str | None = None
    scores: Mapping[str, float] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for name in ("name", "version", "text"):
            _text(getattr(self, name), name)
        for name in ("parent_version", "optimizer", "dataset_id", "dataset_version"):
            if getattr(self, name) is not None:
                _text(getattr(self, name), name)
        if self.parent_version == self.version:
            raise DomainValidationError("A version cannot be its own parent")
        if (self.dataset_id is None) != (self.dataset_version is None):
            raise DomainValidationError("dataset_id and dataset_version must be paired")
        object.__setattr__(self, "status", _enum(self.status, PromptStatus, "status"))
        scores = _mapping(self.scores, "scores")
        for name, score in scores.items():
            _text(name, "metric name")
            _score(score, name)
        object.__setattr__(self, "scores", scores)
        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise DomainValidationError("created_at must be timezone-aware")


@dataclass(frozen=True)
class EvaluationCase:
    """One JSON-compatible dataset case; context and tags are optional."""

    id: str
    input: str
    expected: Mapping[str, Any]
    context: Mapping[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.id, "id")
        _text(self.input, "input")
        object.__setattr__(self, "expected", _mapping(self.expected, "expected"))
        object.__setattr__(self, "context", _mapping(self.context, "context"))
        object.__setattr__(self, "tags", _strings(self.tags, "tags"))


@dataclass(frozen=True)
class MetricResult:
    """Normalized evaluator score and optional failure explanation."""

    name: str
    score: float
    passed: bool
    critical: bool = False
    details: str | None = None

    def __post_init__(self) -> None:
        _text(self.name, "name")
        _score(self.score, "score")
        if not isinstance(self.passed, bool) or not isinstance(self.critical, bool):
            raise DomainValidationError("passed and critical must be booleans")
        if self.details is not None and not isinstance(self.details, str):
            raise DomainValidationError("details must be a string or None")


@dataclass(frozen=True)
class CaseEvaluationResult:
    """Output and evaluator results for one case."""

    case_id: str
    output: Mapping[str, Any]
    metrics: tuple[MetricResult, ...]

    def __post_init__(self) -> None:
        _text(self.case_id, "case_id")
        object.__setattr__(self, "output", _mapping(self.output, "output"))
        if not isinstance(self.metrics, (list, tuple)) or not all(
            isinstance(metric, MetricResult) for metric in self.metrics
        ):
            raise DomainValidationError("metrics must contain MetricResult models")
        if len({metric.name for metric in self.metrics}) != len(self.metrics):
            raise DomainValidationError("Duplicate metric names in case")
        object.__setattr__(self, "metrics", tuple(self.metrics))

    @property
    def critical_failures(self) -> tuple[str, ...]:
        return tuple(m.name for m in self.metrics if m.critical and not m.passed)


@dataclass(frozen=True)
class EvaluationResult:
    """Detailed run results and normalized aggregate metric scores."""

    prompt_name: str
    prompt_version: str
    dataset_id: str
    dataset_version: str
    cases: tuple[CaseEvaluationResult, ...]
    scores: Mapping[str, float]

    def __post_init__(self) -> None:
        for name in ("prompt_name", "prompt_version", "dataset_id", "dataset_version"):
            _text(getattr(self, name), name)
        if not isinstance(self.cases, (list, tuple)) or not self.cases or not all(
            isinstance(case, CaseEvaluationResult) for case in self.cases
        ):
            raise DomainValidationError("cases must contain case results and be nonempty")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise DomainValidationError("Duplicate case ids in evaluation")
        object.__setattr__(self, "cases", tuple(self.cases))
        scores = _mapping(self.scores, "scores")
        for name, score in scores.items():
            _text(name, "metric name")
            _score(score, name)
        object.__setattr__(self, "scores", scores)

    @property
    def failed_case_ids(self) -> tuple[str, ...]:
        return tuple(c.case_id for c in self.cases if any(not m.passed for m in c.metrics))

    @property
    def critical_failures(self) -> Mapping[str, tuple[str, ...]]:
        return MappingProxyType({
            c.case_id: c.critical_failures for c in self.cases if c.critical_failures
        })


@dataclass(frozen=True)
class OptimizationTask:
    """Provider-independent optimizer input with separate train/validation cases."""

    task_id: str
    source_prompt: PromptVersion
    train_cases: tuple[EvaluationCase, ...]
    validation_cases: tuple[EvaluationCase, ...]
    dataset_id: str
    dataset_version: str

    def __post_init__(self) -> None:
        for name in ("task_id", "dataset_id", "dataset_version"):
            _text(getattr(self, name), name)
        if not isinstance(self.source_prompt, PromptVersion):
            raise DomainValidationError("source_prompt must be a PromptVersion")
        for name in ("train_cases", "validation_cases"):
            cases = getattr(self, name)
            if not isinstance(cases, (list, tuple)) or not cases or not all(
                isinstance(case, EvaluationCase) for case in cases
            ):
                raise DomainValidationError(f"{name} must contain nonempty evaluation cases")
            if len({case.id for case in cases}) != len(cases):
                raise DomainValidationError(f"Duplicate case ids in {name}")
            object.__setattr__(self, name, tuple(cases))


@dataclass(frozen=True)
class OptimizationResult:
    """Optimizer output; evaluation and recommendations belong to the pipeline."""

    task_id: str
    candidate: PromptVersion
    optimizer: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text(self.task_id, "task_id")
        _text(self.optimizer, "optimizer")
        if not isinstance(self.candidate, PromptVersion):
            raise DomainValidationError("candidate must be a PromptVersion")
        if self.candidate.status is not PromptStatus.CANDIDATE:
            raise DomainValidationError("Optimizer output must have candidate status")
        object.__setattr__(self, "metadata", _mapping(self.metadata, "metadata"))
