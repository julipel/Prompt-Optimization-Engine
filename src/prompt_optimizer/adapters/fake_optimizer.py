"""Offline optimizer with an explicitly supplied candidate, no inference."""

from prompt_optimizer.domain import OptimizationResult, OptimizationTask
from .optimizer_result import make_result, validate_candidate_version


class FakeOptimizer:
    def __init__(self, candidate_text: str) -> None:
        if not isinstance(candidate_text, str) or not candidate_text.strip():
            raise ValueError("candidate_text must be a nonempty string")
        self.candidate_text = candidate_text
        self.calls: list[OptimizationTask] = []

    def optimize(self, task: OptimizationTask, *, candidate_version: str) -> OptimizationResult:
        validate_candidate_version(task, candidate_version)
        self.calls.append(task)
        return make_result(task, candidate_version=candidate_version,
                           text=self.candidate_text, optimizer="fake", metadata={"offline": True})
