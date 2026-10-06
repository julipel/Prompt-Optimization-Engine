"""Explicit registry actions; evaluation recommendations never invoke these."""
from dataclasses import replace
from prompt_optimizer.domain import OptimizationPipelineResult, PromptVersion
from prompt_optimizer.domain.registry import RegistryProvenance
from prompt_optimizer.ports import PromptRepository


class SaveCandidate:
    def __init__(self, repository: PromptRepository) -> None:
        self.repository = repository

    def execute(self, result: OptimizationPipelineResult) -> PromptVersion:
        if not isinstance(result, OptimizationPipelineResult):
            raise ValueError("result must be OptimizationPipelineResult")
        candidate = replace(result.candidate, scores=result.candidate_evaluation.scores)
        provenance = RegistryProvenance(result.task_id, result.optimization.metadata)
        return self.repository.create(candidate, provenance=provenance)


class ApprovePrompt:
    def __init__(self, repository: PromptRepository) -> None:
        self.repository = repository

    def execute(self, name: str, version: str) -> PromptVersion:
        return self.repository.approve(name, version)


class RejectPrompt:
    def __init__(self, repository: PromptRepository) -> None:
        self.repository = repository

    def execute(self, name: str, version: str) -> PromptVersion:
        return self.repository.reject(name, version)


class PromotePrompt:
    def __init__(self, repository: PromptRepository) -> None:
        self.repository = repository

    def execute(self, name: str, version: str) -> PromptVersion:
        return self.repository.promote(name, version)
