from dataclasses import FrozenInstanceError, replace
from unittest.mock import Mock

import pytest

from prompt_optimizer.adapters.fake_llm import FakeLLMClient
from prompt_optimizer.adapters.fake_optimizer import FakeOptimizer
from prompt_optimizer.application import CompareVersions, EvaluatePrompt, OptimizePrompt, PipelineError
from prompt_optimizer.domain import (CaseEvaluationResult, Dataset, DomainValidationError, EvaluationCase,
    EvaluationResult, MetricResult, OptimizationTask, PromptStatus, PromptVersion, RegressionThresholds)
from prompt_optimizer.domain.evaluation import DeterministicEvaluator, aggregate_metrics


def evaluation(version, scores, *, critical=False):
    metrics = tuple(MetricResult(n, s, s == 1) for n, s in scores.items()) + (
        MetricResult("safety", 0 if critical else 1, not critical, critical=True),)
    cases = (CaseEvaluationResult("val", {}, metrics),)
    return EvaluationResult("p", version, "ds", "1", cases, aggregate_metrics(cases))


@pytest.mark.parametrize("before,after,expected", [
    ({"a": .5}, {"a": .8}, "approve"),
    ({"a": .8}, {"a": .5}, "reject"),
    ({"a": .8}, {"a": .8}, "review"),
    ({"a": .5, "b": .5}, {"a": .4, "b": 1}, "reject"),
    ({"a": .5}, {"b": 1}, "review"),
])
def test_decisions(before, after, expected):
    result = CompareVersions().execute(evaluation("v1", before), evaluation("v2", after))
    assert result.recommendation.value == expected
    assert result.before == result.baseline.scores
    assert result.after == result.candidate.scores
    assert all(g.reason for g in result.gates)


@pytest.mark.parametrize("baseline_critical", [False, True])
def test_critical_always_reject(baseline_critical):
    result = CompareVersions().execute(evaluation("v1", {"a": .2}, critical=baseline_critical),
                                       evaluation("v2", {"a": 1}, critical=True))
    assert result.recommendation.value == "reject"
    assert result.candidate.critical_failures == {"val": ("safety",)}


@pytest.mark.parametrize("after,expected", [(.7, "review"), (.699999999, "reject"), (.700000001, "review")])
def test_threshold_boundary(after, expected):
    result = CompareVersions(RegressionThresholds({"a": .1, "aggregate": .1})).execute(
        evaluation("v1", {"a": .8}), evaluation("v2", {"a": after}))
    assert result.recommendation.value == expected


def test_allowed_regression_with_overall_improvement():
    result = CompareVersions(RegressionThresholds({"a": .1})).execute(
        evaluation("v1", {"a": .8, "b": .2}), evaluation("v2", {"a": .7, "b": 1}))
    assert result.recommendation.value == "approve"


@pytest.mark.parametrize("value", [-.1, 1.1, True, float("nan"), float("inf"), "0", None])
@pytest.mark.parametrize("field", ["allowed_decrease", "minimum_scores"])
def test_invalid_thresholds(value, field):
    with pytest.raises(DomainValidationError):
        RegressionThresholds(**{field: {"a": value}})


def test_config_freezes_and_hard_minimum():
    source = {"a": .9}
    config = RegressionThresholds(minimum_scores=source)
    source["a"] = 0
    result = CompareVersions(config).execute(evaluation("v1", {"a": .2}), evaluation("v2", {"a": .8}))
    assert result.recommendation.value == "reject"
    with pytest.raises(TypeError):
        config.minimum_scores["a"] = 0
    with pytest.raises(FrozenInstanceError):
        config.minimum_scores = {}


def test_changed_coverage_or_critical_semantics_needs_review():
    before = evaluation("v1", {"a": .2})
    after = evaluation("v2", {"a": 1})
    metrics = tuple(replace(m, critical=True) if m.name == "a" else m for m in after.cases[0].metrics)
    cases = (replace(after.cases[0], metrics=metrics),)
    after = replace(after, cases=cases, scores=aggregate_metrics(cases))
    result = CompareVersions().execute(before, after)
    assert result.deltas["a"] is None
    assert result.recommendation.value == "review"


@pytest.fixture
def task():
    return OptimizationTask("t", PromptVersion("p", "v1", "original", status="production"),
        (EvaluationCase("train", "Q", {"action": "train"}),),
        (EvaluationCase("val", "Q", {"action": "escalate"}),), "ds", "1")


def runner(task):
    client = FakeLLMClient({}, version_responses={
        ("v1", "val"): {"action": "wrong"}, ("v2", "val"): {"action": "escalate"}})
    return client, EvaluatePrompt(client, [DeterministicEvaluator()])


def test_complete_offline_and_call_order(task):
    client, engine = runner(task)
    events = []
    datasets = []
    original = engine.execute
    def evaluate(prompt, dataset):
        events.append(prompt.version)
        datasets.append(dataset)
        return original(prompt, dataset)
    engine.execute = evaluate
    optimizer = FakeOptimizer("improved")
    optimize = optimizer.optimize
    def optimization(task, *, candidate_version):
        events.append("optimizer")
        return optimize(task, candidate_version=candidate_version)
    optimizer.optimize = optimization
    result = OptimizePrompt(optimizer, engine).execute(task, candidate_version="v2")
    assert events == ["v1", "optimizer", "v2"]
    assert datasets[0] is datasets[1]
    assert datasets[0].cases == task.validation_cases
    assert optimizer.calls == [task]
    assert [c[1] for c in client.calls] == ["val", "val"]
    assert result.recommendation.value == "approve"
    assert result.baseline.failed_case_ids == ("val",)
    assert result.candidate_evaluation.failed_case_ids == ()
    assert result.candidate.status is PromptStatus.CANDIDATE
    assert task.source_prompt.text == "original"
    assert task.source_prompt.status is PromptStatus.PRODUCTION


@pytest.mark.parametrize("stage", ["baseline_evaluation", "optimizer", "candidate_evaluation"])
def test_stage_failures_preserve_cause(task, stage):
    _, engine = runner(task)
    optimizer = FakeOptimizer("new")
    error = RuntimeError("original cause")
    if stage == "optimizer":
        optimizer.optimize = Mock(side_effect=error)
    else:
        execute = engine.execute
        engine.execute = Mock(side_effect=error if stage == "baseline_evaluation" else
                              [execute(task.source_prompt, Dataset("ds", "1", task.validation_cases)), error])
    with pytest.raises(PipelineError) as caught:
        OptimizePrompt(optimizer, engine).execute(task, candidate_version="v2")
    assert caught.value.stage == stage
    assert caught.value.__cause__ is error


@pytest.mark.parametrize("field,value", [("name", "other"), ("version", "v3"),
    ("parent_version", "v0"), ("optimizer", "other"), ("dataset_id", "other"), ("dataset_version", "2")])
def test_optimizer_contract_before_candidate_evaluation(task, field, value):
    client, engine = runner(task)
    optimizer = FakeOptimizer("new")
    result = optimizer.optimize(task, candidate_version="v2")
    optimizer.optimize = Mock(return_value=replace(result, candidate=replace(result.candidate, **{field: value})))
    with pytest.raises(PipelineError) as caught:
        OptimizePrompt(optimizer, engine).execute(task, candidate_version="v2")
    assert caught.value.stage == "optimizer_contract"
    assert field in str(caught.value.__cause__)
    assert len(client.calls) == 1


@pytest.mark.parametrize("kind", ["task", "type"])
def test_optimizer_wrong_task_or_type(task, kind):
    _, engine = runner(task)
    optimizer = FakeOptimizer("new")
    result = optimizer.optimize(task, candidate_version="v2")
    optimizer.optimize = Mock(return_value=replace(result, task_id="wrong") if kind == "task" else None)
    with pytest.raises(PipelineError, match="optimizer_contract"):
        OptimizePrompt(optimizer, engine).execute(task, candidate_version="v2")


def test_comparison_wrong_dataset_and_inconsistent_scores():
    before = evaluation("v1", {"a": .2})
    after = evaluation("v2", {"a": 1})
    for invalid in (replace(after, dataset_version="2"), replace(after, scores={"aggregate": 0})):
        with pytest.raises(ValueError):
            CompareVersions().execute(before, invalid)


def test_changed_metric_case_coverage():
    before = evaluation("v1", {"a": .2})
    after = evaluation("v2", {"a": 1})
    for result, has_a in ((before, True), (after, False)):
        metrics = (MetricResult("safety", 1, True, critical=True),)
        if has_a:
            metrics += (MetricResult("a", .2, False),)
        cases = result.cases + (CaseEvaluationResult("other", {}, metrics),)
        if has_a:
            before = replace(result, cases=cases, scores=aggregate_metrics(cases))
        else:
            after = replace(result, cases=cases, scores=aggregate_metrics(cases))
    comparison = CompareVersions().execute(before, after)
    assert comparison.deltas["a"] is None
    assert comparison.deltas["aggregate"] is None
    assert comparison.recommendation.value == "review"


@pytest.mark.parametrize("minimum,expected", [(1, "approve"), (.8, "approve")])
def test_hard_minimum_inclusive(minimum, expected):
    result = CompareVersions(RegressionThresholds(minimum_scores={"a": minimum})).execute(
        evaluation("v1", {"a": .2}), evaluation("v2", {"a": 1}))
    assert result.recommendation.value == expected


def test_missing_configured_metric():
    before, after = evaluation("v1", {"a": .2}), evaluation("v2", {"a": 1})
    assert CompareVersions(RegressionThresholds({"absent": 0})).execute(before, after).recommendation.value == "review"
    assert CompareVersions(RegressionThresholds(minimum_scores={"absent": 0})).execute(before, after).recommendation.value == "reject"


def test_critical_only_equal_requires_review():
    result = CompareVersions().execute(evaluation("v1", {}), evaluation("v2", {}))
    assert result.recommendation.value == "review"


def test_malformed_candidate_status_rejected(task):
    client, engine = runner(task)
    optimizer = FakeOptimizer("new")
    result = optimizer.optimize(task, candidate_version="v2")
    # Simulate a port implementation bypassing the domain constructor.
    object.__setattr__(result.candidate, "status", PromptStatus.APPROVED)
    optimizer.optimize = Mock(return_value=result)
    with pytest.raises(PipelineError) as caught:
        OptimizePrompt(optimizer, engine).execute(task, candidate_version="v2")
    assert caught.value.stage == "optimizer_contract"
    assert "status" in str(caught.value.__cause__)
    assert len(client.calls) == 1


@pytest.mark.parametrize("version", ["v1", "", " ", None])
def test_invalid_requested_version_before_any_call(task, version):
    client, engine = runner(task)
    optimizer = FakeOptimizer("new")
    with pytest.raises(ValueError, match="candidate_version"):
        OptimizePrompt(optimizer, engine).execute(task, candidate_version=version)
    assert not client.calls and not optimizer.calls


@pytest.mark.parametrize("version,stage", [("v1", "baseline_evaluation"), ("v2", "candidate_evaluation")])
def test_real_evaluation_errors_retain_nested_cause(task, version, stage):
    responses = {("v1", "val"): {"action": "wrong"}, ("v2", "val"): {"action": "escalate"}}
    del responses[version, "val"]
    engine = EvaluatePrompt(FakeLLMClient({}, version_responses=responses), [DeterministicEvaluator()])
    with pytest.raises(PipelineError) as caught:
        OptimizePrompt(FakeOptimizer("new"), engine).execute(task, candidate_version="v2")
    assert caught.value.stage == stage
    assert caught.value.__cause__.case_id == "val"
    assert isinstance(caught.value.__cause__.__cause__, KeyError)
