"""Immutable configuration and results for validation comparisons."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite

from .exceptions import DomainValidationError
from .models import EvaluationResult, OptimizationResult, PromptVersion, Recommendation, _mapping, _score, _text


@dataclass(frozen=True)
class RegressionThresholds:
    """Maximum absolute score drops; unspecified metrics allow zero drop."""

    allowed_decrease: Mapping[str, float] = field(default_factory=dict)
    minimum_scores: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("allowed_decrease", "minimum_scores"):
            values = _mapping(getattr(self, field_name), field_name)
            for name, value in values.items():
                _text(name, "metric name")
                _score(value, name)
            object.__setattr__(self, field_name, values)


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    reason: str

    def __post_init__(self) -> None:
        _text(self.name, "gate name")
        _text(self.reason, "gate reason")
        if not isinstance(self.passed, bool):
            raise DomainValidationError("gate passed must be boolean")


@dataclass(frozen=True)
class ComparisonResult:
    baseline: EvaluationResult
    candidate: EvaluationResult
    deltas: Mapping[str, float | None]
    gates: tuple[GateResult, ...]
    recommendation: Recommendation

    def __post_init__(self) -> None:
        if not all(isinstance(v, EvaluationResult) for v in (self.baseline, self.candidate)):
            raise DomainValidationError("comparison requires EvaluationResult models")
        values = _mapping(self.deltas, "deltas")
        for name, value in values.items():
            _text(name, "metric name")
            if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int))
                                      or not isfinite(value) or not -1 <= value <= 1):
                raise DomainValidationError("delta must be None or a finite number in [-1, 1]")
        object.__setattr__(self, "deltas", values)
        if not isinstance(self.gates, (tuple, list)) or not self.gates or not all(
            isinstance(g, GateResult) for g in self.gates
        ):
            raise DomainValidationError("gates must contain GateResult models")
        object.__setattr__(self, "gates", tuple(self.gates))
        try:
            object.__setattr__(self, "recommendation", Recommendation(self.recommendation))
        except (ValueError, TypeError) as exc:
            raise DomainValidationError("invalid recommendation") from exc

    @property
    def before(self) -> Mapping[str, float]:
        return self.baseline.scores

    @property
    def after(self) -> Mapping[str, float]:
        return self.candidate.scores


@dataclass(frozen=True)
class OptimizationPipelineResult:
    task_id: str
    optimization: OptimizationResult
    comparison: ComparisonResult

    def __post_init__(self) -> None:
        _text(self.task_id, "task_id")
        if not isinstance(self.optimization, OptimizationResult) or not isinstance(self.comparison, ComparisonResult):
            raise DomainValidationError("pipeline requires optimization and comparison models")
        c = self.optimization.candidate
        e = self.comparison.candidate
        if self.task_id != self.optimization.task_id or (c.name, c.version, c.dataset_id, c.dataset_version) != (
            e.prompt_name, e.prompt_version, e.dataset_id, e.dataset_version
        ):
            raise DomainValidationError("pipeline result identity mismatch")

    @property
    def candidate(self) -> PromptVersion:
        return self.optimization.candidate

    @property
    def baseline(self) -> EvaluationResult:
        return self.comparison.baseline

    @property
    def candidate_evaluation(self) -> EvaluationResult:
        return self.comparison.candidate

    @property
    def recommendation(self) -> Recommendation:
        return self.comparison.recommendation
