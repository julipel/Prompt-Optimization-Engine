from dataclasses import FrozenInstanceError, replace
from datetime import datetime

import pytest

from prompt_optimizer.domain import (
    CaseEvaluationResult, DomainValidationError, EvaluationCase, EvaluationResult,
    MetricResult, OptimizationResult, OptimizationTask, PromptStatus,
    PromptVersion, Recommendation,
)


def prompt(**kwargs):
    return PromptVersion(name="clinic", version="v001", text="Use known facts", **kwargs)


def case():
    return EvaluationCase(id="price", input="Price?", expected={"price": 3500})


def test_prompt_defaults_and_provenance():
    original = prompt()
    assert original.status is PromptStatus.CANDIDATE
    assert original.created_at.utcoffset() is not None
    candidate = replace(original, version="v002", parent_version="v001", optimizer="fake")
    assert candidate.parent_version == original.version
    assert candidate.text == original.text


@pytest.mark.parametrize("status", list(PromptStatus))
def test_valid_statuses(status):
    assert prompt(status=status.value).status is status


@pytest.mark.parametrize("changes", [
    {"name": " "}, {"version": ""}, {"text": None}, {"status": "unknown"},
    {"parent_version": "v001"}, {"dataset_id": "demo"},
    {"created_at": datetime(2026, 1, 1)}, {"scores": {"success": 2}},
])
def test_invalid_prompt(changes):
    with pytest.raises(DomainValidationError):
        replace(prompt(), **changes)


def test_identity_and_nested_content_are_immutable():
    data = {"knowledge": {"prices": [3500]}}
    model = EvaluationCase(id="price", input="Price?", expected={}, context=data)
    data["knowledge"]["prices"].append(9000)
    assert model.context["knowledge"]["prices"] == (3500,)
    with pytest.raises(TypeError):
        model.context["knowledge"]["new"] = True
    version = prompt(scores={"success": 1})
    with pytest.raises(FrozenInstanceError):
        version.version = "v002"
    with pytest.raises(FrozenInstanceError):
        version.text = "changed"
    with pytest.raises(TypeError):
        version.scores["success"] = 0


def test_optional_case_fields():
    assert dict(case().context) == {}
    assert case().tags == ()


@pytest.mark.parametrize("changes", [
    {"id": ""}, {"input": 123}, {"expected": []}, {"context": None},
    {"tags": "price"}, {"tags": [1]}, {"expected": {1: "bad"}},
    {"context": {"bad": object()}}, {"expected": {"price": float("nan")}},
])
def test_invalid_case(changes):
    with pytest.raises(DomainValidationError):
        replace(case(), **changes)


@pytest.mark.parametrize("score", [-1, 1.01, float("nan"), float("inf"), True, "1"])
def test_invalid_metric_scores(score):
    with pytest.raises(DomainValidationError):
        MetricResult("task_success", score, True)


def test_metric_boolean_validation():
    with pytest.raises(DomainValidationError):
        MetricResult("task_success", 1, "yes")


def test_detailed_evaluation_preserves_critical_failures():
    failed = CaseEvaluationResult("unknown", {"price": 500}, (
        MetricResult("price_hallucination", 0, False, critical=True),
    ))
    passed = CaseEvaluationResult("known", {"price": 3500}, (
        MetricResult("task_success", 1, True),
    ))
    result = EvaluationResult("clinic", "v001", "demo", "1", (failed, passed),
                              {"task_success": 0.5})
    assert result.failed_case_ids == ("unknown",)
    assert dict(result.critical_failures) == {"unknown": ("price_hallucination",)}
    with pytest.raises(DomainValidationError):
        replace(result, cases=(failed, failed))
    with pytest.raises(DomainValidationError):
        replace(result, cases=())
    with pytest.raises(DomainValidationError):
        replace(result, scores={"task_success": -0.1})
    with pytest.raises(DomainValidationError):
        replace(failed, metrics=failed.metrics * 2)


def test_optimizer_contracts():
    task = OptimizationTask("task-1", prompt(), [case()], [case()], "demo", "1")
    assert task.train_cases == (case(),)
    result = OptimizationResult(task.task_id, prompt(), "fake", {"attempts": 1})
    assert result.candidate.status is PromptStatus.CANDIDATE
    for changes in ({"train_cases": ()}, {"validation_cases": ["bad"]},
                    {"train_cases": [case(), case()]}, {"source_prompt": None}):
        with pytest.raises(DomainValidationError):
            replace(task, **changes)
    for status in PromptStatus:
        if status is not PromptStatus.CANDIDATE:
            with pytest.raises(DomainValidationError):
                replace(result, candidate=prompt(status=status))


def test_recommendation_values_are_separate_from_status():
    assert {item.value for item in Recommendation} == {"approve", "reject", "review"}
    with pytest.raises(ValueError):
        Recommendation("production")
