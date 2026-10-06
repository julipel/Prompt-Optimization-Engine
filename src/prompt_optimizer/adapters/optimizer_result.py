"""Shared candidate mapping for optimizer adapters."""

from collections.abc import Mapping
from typing import Any

from prompt_optimizer.domain import OptimizationResult, OptimizationTask, PromptVersion


def make_result(task: OptimizationTask, *, candidate_version: str, text: str,
                optimizer: str, metadata: Mapping[str, Any]) -> OptimizationResult:
    candidate = PromptVersion(
        name=task.source_prompt.name, version=candidate_version, text=text,
        parent_version=task.source_prompt.version, optimizer=optimizer,
        dataset_id=task.dataset_id, dataset_version=task.dataset_version,
    )
    return OptimizationResult(task.task_id, candidate, optimizer, metadata)


def validate_candidate_version(task: OptimizationTask, version: str) -> None:
    if not isinstance(version, str) or not version.strip():
        raise ValueError("candidate_version must be a nonempty string")
    if version == task.source_prompt.version:
        raise ValueError("candidate_version must differ from source version")
