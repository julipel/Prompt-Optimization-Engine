"""Composition roots. Importing this module constructs nothing and imports no DSPy."""
from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
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


NEUTRAL_OUTPUT_DESCRIPTION = ("JSON object with exactly the fields the instructions require. "
                              "Put every factual claim in a field; do not hide claims in free text.")


@dataclass(frozen=True)
class RealConfiguration:
    """Real-provider service settings; the API key itself is never stored here."""

    registry_root: str
    reports_root: str
    model: str
    reflection_model: str
    credential_env: str = "OPENAI_API_KEY"
    max_tokens: int = 16000
    max_metric_calls: int = 150
    output_description: str = NEUTRAL_OUTPUT_DESCRIPTION

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "RealConfiguration":
        missing = [name for name in ("PROMPT_REGISTRY_ROOT", "GEPA_MODEL", "GEPA_REFLECTION_MODEL")
                   if not env.get(name, "").strip()]
        if missing:
            raise ValueError(f"Set environment variables: {', '.join(missing)}")
        registry_root = env["PROMPT_REGISTRY_ROOT"]
        try:
            max_tokens = int(env.get("GEPA_MAX_TOKENS", "16000"))
            max_metric_calls = int(env.get("GEPA_MAX_METRIC_CALLS", "150"))
        except ValueError as exc:
            raise ValueError("GEPA_MAX_TOKENS and GEPA_MAX_METRIC_CALLS must be integers") from exc
        return cls(
            registry_root=registry_root,
            reports_root=env.get("PROMPT_REPORTS_ROOT") or str(Path(registry_root).with_name(
                Path(registry_root).name + "-reports")),
            model=env["GEPA_MODEL"], reflection_model=env["GEPA_REFLECTION_MODEL"],
            credential_env=env.get("PROMPT_OPT_CREDENTIAL_ENV", "OPENAI_API_KEY"),
            max_tokens=max_tokens, max_metric_calls=max_metric_calls,
            output_description=env.get("PROMPT_OUTPUT_DESCRIPTION") or NEUTRAL_OUTPUT_DESCRIPTION,
        )


def build_real_app(config: RealConfiguration) -> FastAPI:
    """GEPA + real-model evaluation on one shared signature; reports persist on disk."""
    from prompt_optimizer.adapters.dspy.gepa_optimizer import GEPAConfig, GEPAOptimizer, _credential
    from prompt_optimizer.adapters.dspy.llm_client import DSPyLLMClient
    from prompt_optimizer.adapters.file_results import FileOptimizationResultStore

    _credential(config.credential_env)  # Fail at startup, not on the first paid request.
    evaluation = EvaluatePrompt(DSPyLLMClient(
        config.model, config.credential_env, max_tokens=config.max_tokens, num_retries=2,
        output_description=config.output_description), [DeterministicEvaluator()])
    optimizer = GEPAOptimizer(GEPAConfig(
        config.model, config.reflection_model, config.credential_env, max_tokens=config.max_tokens,
        max_metric_calls=config.max_metric_calls, output_description=config.output_description))
    return create_app(HttpDependencies(
        FilesystemPromptRepository(config.registry_root), evaluation,
        OptimizePrompt(optimizer, evaluation), FileOptimizationResultStore(config.reports_root),
    ))


def create_real_app() -> FastAPI:
    """Uvicorn factory for the paid real-provider service (configuration from env)."""
    return build_real_app(RealConfiguration.from_env(os.environ))
