import ast
from pathlib import Path
from unittest.mock import Mock

import dspy
import pytest
from dspy.utils import DummyLM

from prompt_optimizer.adapters.dspy.gepa_optimizer import (
    DeterministicFeedback, GEPAConfig, GEPAConfigurationError, GEPAOptimizer,
    MissingCredentialsError, _Program, to_example,
)
from prompt_optimizer.adapters.fake_optimizer import FakeOptimizer
from prompt_optimizer.application import OptimizePrompt, OptimizationError
from prompt_optimizer.domain import EvaluationCase, OptimizationTask, PromptStatus, PromptVersion
from prompt_optimizer.ports import PromptOptimizer


@pytest.fixture
def task():
    train = EvaluationCase("train", "Question", {"action": "escalate", "must_not_invent": True},
                           {"knowledge": {}, "nested": [1, {"a": True}]}, ("critical",))
    val = EvaluationCase("val", "Validation", {"action": "escalate"})
    return OptimizationTask("task-04", PromptVersion("clinic", "v1", "Original instructions",
                            status=PromptStatus.PRODUCTION), (train,), (val,), "clinic-data", "2")


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("TEST_LM_KEY", "secret-for-tests")
    return GEPAConfig("provider/student", "provider/reflect", "TEST_LM_KEY", max_metric_calls=10)


def test_example_maps_json_and_only_inference_inputs(task):
    example = to_example(task.train_cases[0])
    assert dict(example.inputs()) == {"input": "Question", "context": {
        "knowledge": {}, "nested": [1, {"a": True}]}}
    assert example.case_id == "train"
    assert example.expected == {"action": "escalate", "must_not_invent": True}
    assert example.tags == ["critical"]
    example.context["nested"][1]["a"] = False
    assert task.train_cases[0].context["nested"][1]["a"] is True


@pytest.mark.parametrize("output,critical", [
    ({"action": "escalate"}, False),
    ({"action": "escalate", "price": 2000}, True),
    ({"action": "escalate", "unsafe_action": True}, True),
])
def test_feedback_reuses_metrics_and_critical_precedence(task, output, critical):
    feedback = DeterministicFeedback(task)
    pred = dspy.Prediction(output=output)
    overall = feedback(to_example(task.train_cases[0]), pred)
    predictor = feedback(to_example(task.train_cases[0]), pred, [], "respond", [])
    assert overall.score == predictor.score
    assert overall.score == (feedback.failure_score if critical else 1.0)
    assert "critical_failures=" in overall.feedback
    assert "price_hallucination:" in overall.feedback
    assert "instruction_following:" in overall.feedback
    assert "unsafe_action:" in overall.feedback


def test_critical_failure_dominates_entire_split_average(task):
    cases = tuple(EvaluationCase(str(i), "Q", {"action": "escalate"}) for i in range(20))
    task = OptimizationTask(task.task_id, task.source_prompt, cases, cases, "ds", "1")
    feedback = DeterministicFeedback(task)
    good = feedback(to_example(cases[0]), dspy.Prediction(output={"action": "escalate"})).score
    bad = feedback(to_example(cases[1]), dspy.Prediction(output={"action": "escalate", "unsafe_action": True}))
    assert "task_success: score=1.0" in bad.feedback
    assert (19 * good + bad.score) / 20 < 0
    assert feedback(to_example(cases[0]), dspy.Prediction(output={"action": "wrong"})).score == 0


def test_booking_feedback_and_critical_only(task):
    feedback = DeterministicFeedback(task)
    booking = EvaluationCase("booking", "Q", {"intent": "booking", "must_not_claim_booking": True})
    result = feedback(to_example(booking), dspy.Prediction(output={"intent": "booking", "booking_confirmed": True}))
    assert result.score < 0
    assert "fake_booking_confirmation" in result.feedback
    only = EvaluationCase("safety", "Q", {})
    assert feedback(to_example(only), dspy.Prediction(output={})).score == 1


@pytest.mark.parametrize("output", [None, [], "free text", {"price": float("nan")}])
def test_invalid_output_is_error_not_success(task, output):
    feedback = DeterministicFeedback(task)
    with pytest.raises(Exception):
        feedback(to_example(task.train_cases[0]), dspy.Prediction(output=output))
    assert len(feedback.errors) == 1


@pytest.mark.parametrize("changes", [
    {"model": "no-provider"}, {"reflection_model": ""}, {"model": "p/has whitespace"},
    {"credential_env": ""}, {"reflection_credential_env": "bad-key"},
    {"max_metric_calls": 0}, {"max_metric_calls": True}, {"max_tokens": -1},
    {"temperature": float("nan")}, {"temperature": True}, {"temperature": 3},
    {"seed": -1}, {"seed": True}, {"api_base": "not-url"}, {"reflection_api_base": 3},
])
def test_invalid_config(changes):
    values = dict(model="provider/student", reflection_model="provider/reflect", credential_env="KEY")
    with pytest.raises(GEPAConfigurationError):
        GEPAConfig(**(values | changes))


@pytest.mark.parametrize("reflection", [False, True])
def test_missing_credentials_fail_before_dspy(task, config, monkeypatch, reflection):
    if reflection:
        from dataclasses import replace
        config = replace(config, reflection_credential_env="REFLECT_KEY")
        monkeypatch.setenv("REFLECT_KEY", " ")
        expected = "REFLECT_KEY"
    else:
        monkeypatch.delenv("TEST_LM_KEY")
        expected = "TEST_LM_KEY"
    boundary = Mock()
    monkeypatch.setattr(dspy, "LM", boundary)
    with pytest.raises(MissingCredentialsError, match=expected):
        GEPAOptimizer(config).optimize(task, candidate_version="v2")
    boundary.assert_not_called()


def mock_boundary(monkeypatch):
    lm = Mock(side_effect=[object(), object()])
    optimizer = Mock()
    optimized = _Program("Improved instructions")
    optimizer.compile.return_value = optimized
    factory = Mock(return_value=optimizer)
    monkeypatch.setattr(dspy, "LM", lm)
    monkeypatch.setattr(dspy, "GEPA", factory)
    return lm, factory, optimizer


@pytest.mark.parametrize("kind", ["fake", "gepa"])
def test_same_application_port_and_candidate_provenance(task, config, monkeypatch, kind):
    if kind == "gepa":
        lm, factory, boundary = mock_boundary(monkeypatch)
        optimizer: PromptOptimizer = GEPAOptimizer(config)
    else:
        optimizer = FakeOptimizer("Improved instructions")
    from prompt_optimizer.application import EvaluatePrompt
    from prompt_optimizer.adapters.fake_llm import FakeLLMClient
    from prompt_optimizer.domain.evaluation import DeterministicEvaluator
    pipeline = OptimizePrompt(optimizer, EvaluatePrompt(
        FakeLLMClient({"val": {"action": "escalate"}}), [DeterministicEvaluator()]
    )).execute(task, candidate_version="v2")
    result = pipeline.optimization
    assert result.task_id == task.task_id
    candidate = result.candidate
    assert candidate.text == "Improved instructions"
    assert candidate.name == "clinic"
    assert candidate.version == "v2"
    assert candidate.parent_version == "v1"
    assert candidate.dataset_id == "clinic-data"
    assert candidate.dataset_version == "2"
    assert candidate.optimizer == result.optimizer == kind
    assert candidate.status is PromptStatus.CANDIDATE
    assert candidate.scores == {}
    assert candidate.created_at.utcoffset() is not None
    assert task.source_prompt.status is PromptStatus.PRODUCTION
    if kind == "gepa":
        kwargs = boundary.compile.call_args.kwargs
        assert [e.case_id for e in kwargs["trainset"]] == ["train"]
        assert [e.case_id for e in kwargs["valset"]] == ["val"]
        assert boundary.compile.call_args.args[0].respond.signature.instructions == task.source_prompt.text
        metric = factory.call_args.kwargs["metric"]
        assert metric(kwargs["trainset"][0], dspy.Prediction(output={"action": "escalate"})).score == 1
        assert lm.call_count == 2
        assert factory.call_args.kwargs["reflection_lm"] is not None
        assert factory.call_args.kwargs["max_metric_calls"] == 10
        assert factory.call_args.kwargs["failure_score"] < 0
        assert result.metadata["dspy_version"] == "3.4.0"
        assert result.metadata["gepa_version"] == "0.1.4"
        assert "secret-for-tests" not in str(result)
        assert "api_key" not in str(result.metadata)
    else:
        assert optimizer.calls == [task]


@pytest.mark.parametrize("stage", ["lm", "compile", "mapping", "swallowed_feedback"])
def test_optimizer_errors_are_explicit(task, config, monkeypatch, stage):
    lm, factory, boundary = mock_boundary(monkeypatch)
    if stage == "lm":
        lm.side_effect = RuntimeError("provider error")
    elif stage == "compile":
        boundary.compile.side_effect = RuntimeError("execution error")
    elif stage == "mapping":
        boundary.compile.return_value = _Program(" ")
    else:
        def swallowed(student, *, trainset, valset):
            metric = factory.call_args.kwargs["metric"]
            try:
                metric(trainset[0], dspy.Prediction(output="invalid"))
            except Exception:
                pass
            return _Program("Looks successful")
        boundary.compile.side_effect = swallowed
    with pytest.raises(OptimizationError) as error:
        GEPAOptimizer(config).optimize(task, candidate_version="v2")
    assert error.value.__cause__ is not None


@pytest.mark.parametrize("version", ["", " ", "v1", None])
def test_invalid_candidate_version_before_boundary(task, config, monkeypatch, version):
    lm, _, _ = mock_boundary(monkeypatch)
    with pytest.raises(ValueError, match="candidate_version"):
        GEPAOptimizer(config).optimize(task, candidate_version=version)
    lm.assert_not_called()


def test_real_installed_gepa_compile_offline(task, config, monkeypatch):
    """Real GEPA, Module, Predict, Example, JSONAdapter and metric; only LM is offline."""
    from dataclasses import replace
    dummy = DummyLM([{"output": {"action": "escalate"}}] * 10, adapter=dspy.JSONAdapter())
    monkeypatch.setattr(dspy, "LM", lambda *args, **kwargs: dummy)
    previous_lm = dspy.settings.lm
    result = GEPAOptimizer(replace(config, max_metric_calls=1)).optimize(task, candidate_version="v2")
    assert result.candidate.text == task.source_prompt.text
    assert dummy.history
    assert dspy.settings.lm is previous_lm
    messages = str(dummy.history[0]["messages"])
    assert "must_not_invent" not in messages
    assert "case_id" not in messages


def test_core_does_not_import_external_optimizer_dependencies():
    root = Path(__file__).resolve().parents[1] / "src" / "prompt_optimizer"
    for layer in ("domain", "application", "ports"):
        for path in (root / layer).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                assert not any(name.startswith(("dspy", "gepa", "prompt_optimizer.adapters")) for name in names)


def test_real_gepa_reflection_and_candidate_mapping_offline(task, config, monkeypatch):
    from dataclasses import replace
    student = DummyLM([{"output": {"action": "wrong"}}] * 2 +
                      [{"output": {"action": "escalate"}}] * 20, adapter=dspy.JSONAdapter())
    reflection = DummyLM([{"answer": "```\nImproved offline instructions\n```"}] * 10)
    models = iter([student, reflection])
    monkeypatch.setattr(dspy, "LM", lambda *args, **kwargs: next(models))
    result = GEPAOptimizer(replace(config, max_metric_calls=6)).optimize(task, candidate_version="v2")
    assert reflection.history
    assert result.candidate.text == "Improved offline instructions"
    assert "task_success" in str(reflection.history[0])


def test_real_gepa_does_not_hide_generation_errors(task, config, monkeypatch):
    from dataclasses import replace
    dummy = DummyLM([], adapter=dspy.JSONAdapter())
    monkeypatch.setattr(dspy, "LM", lambda *args, **kwargs: dummy)
    with pytest.raises(OptimizationError, match="generation"):
        GEPAOptimizer(replace(config, max_metric_calls=1)).optimize(task, candidate_version="v2")


def test_real_lm_and_gepa_construction_no_provider_calls(task, monkeypatch):
    monkeypatch.setenv("STUDENT_TEST_KEY", "student-secret")
    monkeypatch.setenv("REFLECTION_TEST_KEY", "reflection-secret")
    config = GEPAConfig("openai/gpt-4o-mini", "openai/gpt-4o-mini", "STUDENT_TEST_KEY",
                        "REFLECTION_TEST_KEY", api_base="https://student.example/v1",
                        reflection_api_base="https://reflection.example/v1")
    previous_lm = dspy.settings.lm
    calls = []

    def compile_only(self, student, *, trainset, valset):
        calls.append((dspy.settings.lm, self.reflection_lm))
        assert isinstance(dspy.settings.adapter, dspy.JSONAdapter)
        assert self.metric_fn is not None
        return _Program("Candidate instructions")

    # Keep actual constructors; replace only the method that would call the provider.
    monkeypatch.setattr(dspy.GEPA, "compile", compile_only)
    result = GEPAOptimizer(config).optimize(task, candidate_version="v2")
    student, reflection = calls[0]
    assert isinstance(student, dspy.LM)
    assert isinstance(reflection, dspy.LM)
    assert student.kwargs["api_key"] == "student-secret"
    assert reflection.kwargs["api_key"] == "reflection-secret"
    assert student.kwargs["api_base"] == config.api_base
    assert reflection.kwargs["api_base"] == config.reflection_api_base
    assert student.cache is False
    assert student.num_retries == 0
    assert not student.history and not reflection.history
    assert dspy.settings.lm is previous_lm
    assert "secret" not in str(result.metadata)
