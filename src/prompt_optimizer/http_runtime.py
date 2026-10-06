"""Offline composition root. Importing this module does not construct a registry."""
from collections.abc import Mapping
from dataclasses import dataclass
import os
from typing import Any
from fastapi import FastAPI
from prompt_optimizer.adapters.fake_llm import FakeLLMClient
from prompt_optimizer.adapters.fake_optimizer import FakeOptimizer
from prompt_optimizer.adapters.filesystem_registry import FilesystemPromptRepository
from prompt_optimizer.adapters.memory_results import InMemoryOptimizationResultStore
from prompt_optimizer.application import EvaluatePrompt, OptimizePrompt
from prompt_optimizer.domain.evaluation import DeterministicEvaluator
from prompt_optimizer.interfaces.http import HttpDependencies, create_app


@dataclass(frozen=True)
class OfflineConfiguration:
    registry_root: str
    candidate_text: str
    version_responses: Mapping[tuple[str, str], Mapping[str, Any]]


def build_offline_app(config: OfflineConfiguration) -> FastAPI:
    evaluation = EvaluatePrompt(FakeLLMClient({}, version_responses=config.version_responses),
                                [DeterministicEvaluator()])
    return create_app(HttpDependencies(
        FilesystemPromptRepository(config.registry_root), evaluation,
        OptimizePrompt(FakeOptimizer(config.candidate_text), evaluation),
        InMemoryOptimizationResultStore(),
    ))


def create_demo_app() -> FastAPI:
    """Uvicorn factory for the explicit README demo fixture, never real providers."""
    return build_offline_app(OfflineConfiguration(
        registry_root=os.environ["PROMPT_REGISTRY_ROOT"],
        candidate_text="Не выдумывай цены. Передавай неизвестные вопросы оператору.",
        version_responses={
            ("v001", "unknown"): {"action": "answer"},
            ("v002", "unknown"): {"action": "escalate", "note": "Проверено"},
            ("v003", "unknown"): {"action": "escalate"},
        },
    ))
