import os

import pytest


@pytest.mark.gepa_smoke
def test_real_provider_gepa(request):
    credential_env = os.environ.get("GEPA_CREDENTIAL_ENV", "OPENAI_API_KEY")
    reflection_env = os.environ.get("GEPA_REFLECTION_CREDENTIAL_ENV", credential_env)
    for name in (credential_env, reflection_env):
        if not os.environ.get(name, "").strip():
            pytest.skip(f"GEPA smoke requires credentials in {name}; no API calls made")
    if not request.config.getoption("--run-gepa-smoke"):
        pytest.skip("Real-provider calls require explicit --run-gepa-smoke opt-in")
    model = os.environ.get("GEPA_MODEL")
    reflection_model = os.environ.get("GEPA_REFLECTION_MODEL")
    if not model or not reflection_model:
        pytest.skip("Set GEPA_MODEL and GEPA_REFLECTION_MODEL to explicitly select paid models")
    from prompt_optimizer.adapters.dspy.gepa_optimizer import GEPAConfig, GEPAOptimizer
    from prompt_optimizer.application import OptimizePrompt
    from prompt_optimizer.domain import EvaluationCase, OptimizationTask, PromptVersion
    train = EvaluationCase("train", "Unknown service price?", {"action": "escalate", "must_not_invent": True})
    validation = EvaluationCase("validation", "Unknown treatment price?", {"action": "escalate", "must_not_invent": True})
    task = OptimizationTask("smoke", PromptVersion("smoke", "v1", "Escalate unknown prices. Never invent facts."),
                            (train,), (validation,), "synthetic-smoke", "1")
    config = GEPAConfig(model, reflection_model, credential_env, reflection_env, max_metric_calls=6,
                        max_tokens=int(os.environ.get("GEPA_MAX_TOKENS", "4096")))
    result = GEPAOptimizer(config).optimize(task, candidate_version="v2")
    assert result.candidate.text.strip()
    assert result.optimizer == "gepa"
