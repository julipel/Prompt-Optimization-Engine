from pathlib import Path

import pytest

from prompt_optimizer.adapters.fake_llm import FakeLLMClient
from prompt_optimizer.adapters.jsonl_dataset import JsonlDatasetLoader
from prompt_optimizer.application import EvaluatePrompt, EvaluationError, load_dataset
from prompt_optimizer.domain import (
    CaseEvaluationResult, Dataset, DomainValidationError, EvaluationCase,
    MetricResult, PromptVersion,
)
from prompt_optimizer.domain.evaluation import DeterministicEvaluator, aggregate_metrics


def metrics(expected, output, context=None):
    case = EvaluationCase("one", "Synthetic question", expected, context or {})
    return {m.name: m for m in DeterministicEvaluator().evaluate(case, output)}


@pytest.mark.parametrize("price,passed", [(3500, True), (3500.0, True), (5000, False),
                                           ("3500", False), (True, False)])
def test_price_correctness_and_grounding(price, passed):
    result = metrics({"intent": "price", "price": 3500, "must_not_invent": True},
                     {"intent": "price", "price": price}, {"knowledge": {"Echo": 3500}})
    assert result["task_success"].passed is passed
    assert result["groundedness"].passed is passed
    assert result["price_hallucination"].passed is passed
    assert result["price_hallucination"].critical
    if not passed:
        assert result["price_hallucination"].details


def test_missing_known_price_fails_task_without_hallucination():
    result = metrics({"price": 3500}, {})
    assert not result["task_success"].passed
    assert result["price_hallucination"].passed


def test_expected_price_cannot_override_context():
    result = metrics({"price": 3500}, {"price": 3500}, {"knowledge": {}})
    assert result["task_success"].passed
    assert not result["groundedness"].passed
    assert not result["price_hallucination"].passed


@pytest.mark.parametrize("output,passed", [({"action": "escalate"}, True),
    ({"action": "escalate", "price": None}, True),
    ({"action": "escalate", "price": 500}, False),
    ({"action": "escalate", "price": "unknown"}, False)])
def test_unknown_price(output, passed):
    result = metrics({"action": "escalate", "must_not_invent": True}, output,
                     {"knowledge": {}})
    assert result["price_hallucination"].passed is passed
    assert result["instruction_following"].passed is passed


def test_unknown_price_requires_escalation_even_without_hallucination():
    result = metrics({"action": "escalate", "must_not_invent": True}, {})
    assert not result["task_success"].passed
    assert result["price_hallucination"].passed


@pytest.mark.parametrize("output,passed", [({"tool": "check_availability"}, True),
    ({"tool": "book"}, False), ({}, False), ({"tool": None}, False)])
def test_tool_selection(output, passed):
    assert metrics({"tool": "check_availability"}, output)["tool_selection"].passed is passed


@pytest.mark.parametrize("confirmed,passed", [(False, True), (True, False), ("false", False)])
def test_forbidden_booking_confirmation(confirmed, passed):
    result = metrics({"intent": "booking", "must_not_claim_booking": True,
                      "must_ask": ["doctor"]},
                     {"intent": "booking", "asked": ["doctor"], "booking_confirmed": confirmed},
                     {"tool_result": {"success": True}})
    assert result["fake_booking_confirmation"].passed is passed
    assert result["fake_booking_confirmation"].critical
    assert result["instruction_following"].passed is passed


@pytest.mark.parametrize("context,passed", [({}, False),
    ({"tool_result": {"success": False}}, False),
    ({"tool_result": {"success": "true"}}, False),
    ({"tool_result": {"success": True}}, True)])
def test_booking_requires_trusted_tool_result(context, passed):
    result = metrics({"intent": "booking"},
                     {"intent": "booking", "booking_confirmed": True,
                      "tool_result": {"success": True}}, context)
    assert result["fake_booking_confirmation"].passed is passed


@pytest.mark.parametrize("asked", [[], ["service"], "doctor", [123]])
def test_missing_or_malformed_required_question(asked):
    result = metrics({"must_ask": ["doctor"]}, {"asked": asked})
    assert not result["instruction_following"].passed


@pytest.mark.parametrize("unsafe", [True, "false", None, 0])
def test_unsafe_action_is_critical(unsafe):
    result = metrics({}, {"unsafe_action": unsafe})
    assert result["unsafe_action"].critical
    assert not result["unsafe_action"].passed


def test_structured_comparison_preserves_json_types_and_sequences():
    expected = {"payload": {"items": [1, {"ok": True}]}}
    assert metrics(expected, {"payload": {"items": [1.0, {"ok": True}]}})["task_success"].passed
    assert not metrics(expected, {"payload": {"items": [1, {"ok": 1}]}})["task_success"].passed


def test_aggregation_uses_applicable_cases_and_excludes_critical_checks():
    cases = (
        CaseEvaluationResult("a", {}, (MetricResult("task_success", 1, True),
            MetricResult("tool_selection", 0, False),
            MetricResult("safety", 0, False, critical=True))),
        CaseEvaluationResult("b", {}, (MetricResult("task_success", 0.5, False),
            MetricResult("safety", 1, True, critical=True))),
    )
    assert aggregate_metrics(cases) == {
        "task_success": 0.75, "tool_selection": 0, "safety": 0.5, "aggregate": 0.375,
    }
    assert "aggregate" not in aggregate_metrics((
        CaseEvaluationResult("c", {}, (MetricResult("safety", 1, True, True),)),
    ))


@pytest.mark.parametrize("cases", [(), (CaseEvaluationResult("a", {}, ()),),
    (CaseEvaluationResult("a", {}, (MetricResult("aggregate", 1, True),)),),
    (CaseEvaluationResult("a", {}, (MetricResult("safety", 1, True),)),
     CaseEvaluationResult("b", {}, (MetricResult("safety", 1, True, True),)))])
def test_invalid_aggregation(cases):
    with pytest.raises(DomainValidationError):
        aggregate_metrics(cases)


def demo(split="validation"):
    root = Path(__file__).resolve().parents[1]
    return load_dataset(str(root / "datasets" / "clinic" / f"{split}.jsonl"),
                        dataset_id="clinic-demo", version="1", loader=JsonlDatasetLoader())


def scripted(split="validation"):
    # Independent fixtures, not copied from case.expected.
    return {
        f"{split}_price_001": {"intent": "price", "price": 2000 if split == "validation" else 3500},
        f"{split}_unknown_price_001": {"intent": "price", "action": "escalate", "price": None},
        f"{split}_booking_001": {"intent": "booking", "asked": ["doctor"],
                               "booking_confirmed": False, "tool": "check_availability"},
        f"{split}_unknown_001": {"intent": "unknown", "action": "escalate"},
    }


def prompt():
    return PromptVersion("clinic", "v001", "Use only known facts")


@pytest.mark.parametrize("split", ["train", "validation"])
def test_demo_baseline_detailed_result(split):
    dataset = demo(split)
    responses = scripted(split)
    client = FakeLLMClient(responses)
    baseline = prompt()
    result = EvaluatePrompt(client, [DeterministicEvaluator()]).execute(baseline, dataset)
    assert (result.prompt_name, result.prompt_version, result.dataset_id, result.dataset_version) == (
        "clinic", "v001", "clinic-demo", "1")
    assert [case.case_id for case in result.cases] == [case.id for case in dataset.cases]
    assert result.failed_case_ids == ()
    assert dict(result.critical_failures) == {}
    assert result.scores["aggregate"] == 1
    assert len(client.calls) == 4
    for case, source, call in zip(result.cases, dataset.cases, client.calls):
        expected_output = CaseEvaluationResult(case.case_id, responses[case.case_id], ()).output
        assert case.output == expected_output
        assert case.metrics and all(m.details for m in case.metrics)
        assert call == (baseline, source.id, source.input, source.context)


def test_critical_failure_survives_high_aggregate_and_other_cases():
    responses = scripted()
    responses["validation_unknown_price_001"]["price"] = 9000
    responses["validation_booking_001"]["booking_confirmed"] = True
    result = EvaluatePrompt(FakeLLMClient(responses), [DeterministicEvaluator()]).execute(prompt(), demo())
    assert result.scores["aggregate"] > 0.5
    assert dict(result.critical_failures) == {
        "validation_unknown_price_001": ("price_hallucination",),
        "validation_booking_001": ("fake_booking_confirmation",),
    }
    assert result.failed_case_ids == ("validation_unknown_price_001", "validation_booking_001")
    assert len(result.cases) == 4


def test_fake_response_snapshot_and_missing_response():
    responses = {"one": {"asked": ["doctor"]}}
    client = FakeLLMClient(responses)
    responses["one"]["asked"].clear()
    output = client.generate(prompt(), case_id="one", input="x", context={})
    assert output["asked"] == ("doctor",)
    with pytest.raises(TypeError):
        output["new"] = True
    with pytest.raises(KeyError, match="missing"):
        client.generate(prompt(), case_id="missing", input="x", context={})


@pytest.mark.parametrize("output", [[], {"price": float("nan")}, {"bad": object()}])
def test_invalid_client_outputs_are_execution_errors(output):
    class BadClient:
        def generate(self, *args, **kwargs):
            return output
    with pytest.raises(EvaluationError) as error:
        EvaluatePrompt(BadClient(), [DeterministicEvaluator()]).execute(prompt(), demo())
    assert error.value.case_id == "validation_price_001"
    assert error.value.stage == "generation"
    assert error.value.__cause__ is not None


def test_missing_response_aborts_run_with_cause():
    with pytest.raises(EvaluationError) as error:
        EvaluatePrompt(FakeLLMClient({}), [DeterministicEvaluator()]).execute(prompt(), demo())
    assert isinstance(error.value.__cause__, KeyError)


@pytest.mark.parametrize("returned", [(), (MetricResult("x", 1, True),) * 2,
                                      ("bad",), (MetricResult("aggregate", 1, True),)])
def test_bad_evaluator_contract(returned):
    class BadEvaluator:
        def evaluate(self, case, output):
            return returned
    with pytest.raises(EvaluationError) as error:
        EvaluatePrompt(FakeLLMClient(scripted()), [BadEvaluator()]).execute(prompt(), demo())
    assert error.value.stage == "evaluation"
    assert error.value.__cause__ is not None


def test_custom_evaluator_and_empty_configuration():
    class CustomEvaluator:
        def evaluate(self, case, output):
            return (MetricResult("custom", 0.25, False, details="Partial credit"),)
    result = EvaluatePrompt(FakeLLMClient(scripted()), [CustomEvaluator()]).execute(prompt(), demo())
    assert dict(result.scores) == {"custom": 0.25, "aggregate": 0.25}
    with pytest.raises(ValueError, match="evaluator"):
        EvaluatePrompt(FakeLLMClient({}), [])


def test_evaluator_exception_has_case_and_original_cause():
    failure = RuntimeError("Evaluator unavailable")

    class BrokenEvaluator:
        def evaluate(self, case, output):
            raise failure

    with pytest.raises(EvaluationError) as error:
        EvaluatePrompt(FakeLLMClient(scripted()), [BrokenEvaluator()]).execute(prompt(), demo())
    assert error.value.case_id == "validation_price_001"
    assert error.value.stage == "evaluation"
    assert error.value.__cause__ is failure


def test_inconsistent_critical_flags_fail_at_aggregation():
    class InconsistentEvaluator:
        def evaluate(self, case, output):
            return (MetricResult("custom", 1, True, critical="booking" in case.tags),)

    with pytest.raises(EvaluationError) as error:
        EvaluatePrompt(FakeLLMClient(scripted()), [InconsistentEvaluator()]).execute(prompt(), demo())
    assert error.value.stage == "aggregation"
    assert isinstance(error.value.__cause__, DomainValidationError)


def test_multiple_evaluators_preserve_additional_critical_failure():
    class AdditionalEvaluator:
        def evaluate(self, case, output):
            return (MetricResult("custom_safety", 0, False, critical=True),)

    result = EvaluatePrompt(FakeLLMClient(scripted()), [
        DeterministicEvaluator(), AdditionalEvaluator(),
    ]).execute(prompt(), demo())
    assert result.scores["aggregate"] == 1
    assert len(result.critical_failures) == 4
    assert all(names == ("custom_safety",) for names in result.critical_failures.values())


@pytest.mark.parametrize("expected", [{"must_not_invent": "true"}, {"must_ask": "doctor"},
                                     {"must_not_claim_booking": 1}])
def test_invalid_expected_controls(expected):
    dataset = Dataset("d", "1", (EvaluationCase("one", "x", expected),))
    with pytest.raises(EvaluationError) as error:
        EvaluatePrompt(FakeLLMClient({"one": {}}), [DeterministicEvaluator()]).execute(prompt(), dataset)
    assert error.value.stage == "evaluation"
    assert isinstance(error.value.__cause__, DomainValidationError)
