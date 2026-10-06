"""DSPy 3.4.0 / GEPA 0.1.4 boundary with deterministic feedback."""

from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import version
from math import isfinite
import os
import re
from typing import Any

import dspy

from prompt_optimizer.application import EvaluatePrompt, OptimizationError
from prompt_optimizer.domain import Dataset, EvaluationCase, OptimizationResult, OptimizationTask
from prompt_optimizer.domain.evaluation import DeterministicEvaluator
from prompt_optimizer.adapters.optimizer_result import make_result, validate_candidate_version


class GEPAConfigurationError(ValueError):
    """Invalid adapter configuration before any model call."""


class MissingCredentialsError(GEPAConfigurationError):
    """Required environment variable is absent or blank."""


@dataclass(frozen=True)
class GEPAConfig:
    model: str
    reflection_model: str
    credential_env: str
    reflection_credential_env: str | None = None
    max_metric_calls: int = 100
    max_tokens: int = 4096
    temperature: float = 1.0
    seed: int = 0
    api_base: str | None = None
    reflection_api_base: str | None = None

    def __post_init__(self) -> None:
        for name in ("model", "reflection_model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r"[^\s/]+/[^\s]+", value):
                raise GEPAConfigurationError(f"{name} must use provider/model notation")
        for name in ("credential_env", "reflection_credential_env"):
            value = getattr(self, name)
            if value is None and name == "reflection_credential_env":
                continue
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                raise GEPAConfigurationError(f"{name} must name an environment variable")
        for name in ("max_metric_calls", "max_tokens"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise GEPAConfigurationError(f"{name} must be a positive integer")
        if type(self.seed) is not int or self.seed < 0:
            raise GEPAConfigurationError("seed must be a nonnegative integer")
        if (isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float))
                or not isfinite(self.temperature) or not 0 <= self.temperature <= 2):
            raise GEPAConfigurationError("temperature must be finite and in [0, 2]")
        for name in ("api_base", "reflection_api_base"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str)
                                      or not re.fullmatch(r"https?://[^\s]+", value)):
                raise GEPAConfigurationError(f"{name} must be an http(s) URL")


def _json(value: Any) -> Any:
    """Thaw domain JSON into DSPy-serializable objects."""
    if isinstance(value, Mapping):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def to_example(case: EvaluationCase) -> dspy.Example:
    return dspy.Example(case_id=case.id, input=case.input, context=_json(case.context),
                        expected=_json(case.expected), tags=list(case.tags)).with_inputs("input", "context")


class _StructuredTask(dspy.Signature):
    input: str = dspy.InputField()
    context: dict[str, Any] = dspy.InputField()
    output: dict[str, Any] = dspy.OutputField(desc=(
        "Structured response: intent, price, action, tool, asked (array of field names), "
        "booking_confirmed and unsafe_action (booleans). Include all factual claims; "
        "do not hide claims in free text."
    ))


class _ExecutionErrors:
    """Keep errors visible across DSPy's candidate deep copies."""

    def __init__(self) -> None:
        self.errors: list[Exception] = []

    def __deepcopy__(self, memo: dict) -> "_ExecutionErrors":
        return self


class _Program(dspy.Module):
    def __init__(self, instructions: str, errors: _ExecutionErrors | None = None) -> None:
        super().__init__()
        self.respond = dspy.Predict(_StructuredTask.with_instructions(instructions))
        self.execution_errors = errors or _ExecutionErrors()

    def forward(self, input: str, context: dict[str, Any]) -> dspy.Prediction:
        try:
            return self.respond(input=input, context=context)
        except Exception as exc:
            self.execution_errors.errors.append(exc)
            raise


class _OutputClient:
    def __init__(self, output: Any) -> None:
        self.output = output

    def generate(self, prompt, *, case_id, input, context):
        return self.output


class DeterministicFeedback:
    """Reuse evaluation API. Negative penalty dominates the whole split mean."""

    def __init__(self, task: OptimizationTask) -> None:
        self.task = task
        self.failure_score = -float(max(len(task.train_cases), len(task.validation_cases)))
        self.errors: list[Exception] = []

    def __call__(self, gold: dspy.Example, pred: dspy.Prediction, trace: Any = None,
                 pred_name: str | None = None, pred_trace: Any = None) -> dspy.Prediction:
        try:
            case = EvaluationCase(gold.case_id, gold.input, gold.expected, gold.context, gold.tags)
            result = EvaluatePrompt(_OutputClient(pred.output), [DeterministicEvaluator()]).execute(
                self.task.source_prompt,
                Dataset(self.task.dataset_id, self.task.dataset_version, (case,)),
            )
            score = (self.failure_score if result.critical_failures
                     else result.scores.get("aggregate", 1.0))
            lines = [f"case={case.id}; critical_failures={dict(result.critical_failures)!r}"]
            lines.extend(f"{m.name}: score={m.score}; passed={m.passed}; critical={m.critical}; {m.details}"
                         for m in result.cases[0].metrics)
            return dspy.Prediction(score=score, feedback="\n".join(lines))
        except Exception as exc:
            # DSPy can swallow metric errors and substitute failure_score. Never
            # return a seemingly successful result after a broken evaluator contract.
            self.errors.append(exc)
            raise


def _credential(env_name: str) -> str:
    key = os.environ.get(env_name, "")
    if not key.strip():
        raise MissingCredentialsError(f"Set nonempty environment variable {env_name} before GEPA optimization")
    return key


class GEPAOptimizer:
    def __init__(self, config: GEPAConfig) -> None:
        if not isinstance(config, GEPAConfig):
            raise GEPAConfigurationError("config must be GEPAConfig")
        self.config = config

    def optimize(self, task: OptimizationTask, *, candidate_version: str) -> OptimizationResult:
        validate_candidate_version(task, candidate_version)
        config = self.config
        key = _credential(config.credential_env)
        reflection_key = _credential(config.reflection_credential_env or config.credential_env)
        feedback = DeterministicFeedback(task)
        execution_errors = _ExecutionErrors()
        try:
            lm = dspy.LM(config.model, api_key=key, api_base=config.api_base,
                         max_tokens=config.max_tokens, temperature=config.temperature,
                         cache=False, num_retries=0)
            reflection_lm = dspy.LM(
                config.reflection_model, api_key=reflection_key,
                api_base=config.reflection_api_base, max_tokens=config.max_tokens,
                temperature=config.temperature, cache=False, num_retries=0,
            )
            with dspy.context(lm=lm, adapter=dspy.JSONAdapter()):
                optimizer = dspy.GEPA(
                    metric=feedback, max_metric_calls=config.max_metric_calls,
                    reflection_lm=reflection_lm, num_threads=1, seed=config.seed,
                    failure_score=feedback.failure_score, use_merge=False,
                    reflection_minibatch_size=min(3, len(task.train_cases)),
                )
                compiled = optimizer.compile(
                    _Program(task.source_prompt.text, execution_errors),
                    trainset=[to_example(case) for case in task.train_cases],
                    valset=[to_example(case) for case in task.validation_cases],
                )
            if feedback.errors:
                raise OptimizationError("GEPA deterministic feedback failed") from feedback.errors[0]
            if execution_errors.errors:
                raise OptimizationError("GEPA model generation failed") from execution_errors.errors[0]
            text = compiled.respond.signature.instructions
            return make_result(
                task, candidate_version=candidate_version, text=text, optimizer="gepa",
                metadata={"dspy_version": version("dspy"), "gepa_version": version("gepa"),
                          "model": config.model, "reflection_model": config.reflection_model,
                          "seed": config.seed, "max_metric_calls": config.max_metric_calls,
                          "train_case_ids": [c.id for c in task.train_cases],
                          "validation_case_ids": [c.id for c in task.validation_cases],
                          "feedback": "deterministic", "critical_failure_score": feedback.failure_score},
            )
        except OptimizationError:
            raise
        except Exception as exc:
            # Exception text from providers may contain secrets; expose only stage.
            raise OptimizationError("GEPA optimization failed during configuration, compilation or result mapping") from exc
