"""Public domain contracts, independent of LLM providers and DSPy."""

from .exceptions import DomainError, DomainValidationError
from .dataset import Dataset
from .comparison import RegressionThresholds, GateResult, ComparisonResult, OptimizationPipelineResult
from .models import (
    CaseEvaluationResult,
    EvaluationCase,
    EvaluationResult,
    MetricResult,
    OptimizationResult,
    OptimizationTask,
    PromptStatus,
    PromptVersion,
    Recommendation,
)

__all__ = [
    "RegressionThresholds", "GateResult", "ComparisonResult", "OptimizationPipelineResult",
    "CaseEvaluationResult", "Dataset", "DomainError", "DomainValidationError",
    "EvaluationCase", "EvaluationResult", "MetricResult", "OptimizationResult",
    "OptimizationTask", "PromptStatus", "PromptVersion", "Recommendation",
]
