"""Provider-independent infrastructure contracts."""

from collections.abc import Mapping
from typing import Any, Protocol

from prompt_optimizer.domain import Dataset, EvaluationCase, MetricResult, PromptVersion
from prompt_optimizer.domain import OptimizationTask, OptimizationResult
from prompt_optimizer.domain.registry import RegistryProvenance, StatusTransition


class PromptRepository(Protocol):
    def create(self, prompt: PromptVersion, *, provenance: RegistryProvenance | None = None) -> PromptVersion: ...
    def bootstrap(self, prompt: PromptVersion) -> PromptVersion: ...
    def read(self, name: str, version: str) -> PromptVersion: ...
    def list(self, name: str) -> tuple[PromptVersion, ...]: ...
    def production(self, name: str) -> PromptVersion | None: ...
    def next_version(self, name: str) -> str: ...
    def approve(self, name: str, version: str) -> PromptVersion: ...
    def reject(self, name: str, version: str) -> PromptVersion: ...
    def promote(self, name: str, version: str) -> PromptVersion: ...
    def history(self, name: str) -> tuple[StatusTransition, ...]: ...
    def provenance(self, name: str, version: str) -> RegistryProvenance: ...


class PromptOptimizer(Protocol):
    """Create a candidate under a caller-supplied immutable version identity."""

    def optimize(self, task: OptimizationTask, *, candidate_version: str) -> OptimizationResult:
        ...


class LLMClient(Protocol):
    """Generate structured JSON; expected answers are never sent to the model."""

    def generate(
        self, prompt: PromptVersion, *, case_id: str, input: str,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...


class Evaluator(Protocol):
    """Return applicable metrics only, with unique names within a case."""

    def evaluate(
        self, case: EvaluationCase, output: Mapping[str, Any],
    ) -> tuple[MetricResult, ...]:
        ...


class DatasetLoader(Protocol):
    """Load cases from an opaque source with caller-supplied dataset identity."""

    def load(self, source: str, *, dataset_id: str, version: str) -> Dataset:
        ...


class ReportWriter(Protocol):
    """Write a versioned report artifact, raising on failure."""
    def write(self, report: Mapping[str, Any], destination: str) -> None: ...
