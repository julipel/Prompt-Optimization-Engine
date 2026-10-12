from pathlib import Path

import dspy
from dspy.utils import DummyLM
import pytest

from prompt_optimizer.adapters.dspy.gepa_optimizer import GEPAConfigurationError, MissingCredentialsError
from prompt_optimizer.adapters.dspy.llm_client import DSPyLLMClient, LLMGenerationError
from prompt_optimizer.adapters.fake_llm import FakeLLMClient
from prompt_optimizer.adapters.jsonl_dataset import JsonlDatasetLoader
from prompt_optimizer.adapters.recording_llm import RecordingLLMClient
from prompt_optimizer.application import EvaluatePrompt
from prompt_optimizer.cli import _responses
from prompt_optimizer.domain import PromptVersion
from prompt_optimizer.domain.evaluation import DeterministicEvaluator

DEMO = Path(__file__).resolve().parents[1] / "datasets" / "clinic_demo"
PROMPT = PromptVersion("clinic", "v001", "Называй только цены из прайса")


def load(split):
    return JsonlDatasetLoader().load(str(DEMO / f"{split}.jsonl"), dataset_id="clinic-demo", version="2")


def test_demo_dataset_is_valid_and_splits_are_disjoint():
    splits = [load(name) for name in ("train", "validation", "test")]
    assert [len(d.cases) for d in splits] == [25, 15, 15]
    for i, first in enumerate(splits):
        for second in splits[i + 1:]:
            assert not {c.id for c in first.cases} & {c.id for c in second.cases}
            assert not {c.input for c in first.cases} & {c.input for c in second.cases}
    categories = {"known_price", "unknown_price", "booking", "off_topic", "medical_advice", "injection"}
    for dataset in splits:
        assert categories <= {tag for case in dataset.cases for tag in case.tags}
        for case in dataset.cases:
            assert DeterministicEvaluator().evaluate(case, {})


def test_demo_expected_values_follow_contract():
    for case in load("train").cases + load("validation").cases + load("test").cases:
        if "must_ask" in case.expected:
            assert set(case.expected["must_ask"]) <= {"услуга", "дата"}
        if "price" in case.expected:
            assert case.expected["price"] in case.context["knowledge"].values()


def test_dspy_client_returns_structured_output_and_applies_prompt_text():
    lm = DummyLM([{"output": {"intent": "price", "price": 1200}}], adapter=dspy.JSONAdapter())
    client = DSPyLLMClient("openai/test-model", "UNSET_TEST_KEY", lm=lm)
    output = client.generate(PROMPT, case_id="ecg", input="ЭКГ?", context={"knowledge": {"ЭКГ": 1200}})
    assert output == {"intent": "price", "price": 1200}
    assert "Называй только цены из прайса" in lm.history[-1]["messages"][0]["content"]


def test_dspy_client_hides_provider_error_text():
    # Unparseable reply: DSPy's error text echoes it, so it must not surface.
    lm = DummyLM([{"unexpected": "SECRET provider echo"}], adapter=dspy.JSONAdapter())
    client = DSPyLLMClient("openai/test-model", "UNSET_TEST_KEY", lm=lm)
    with pytest.raises(LLMGenerationError) as raised:
        client.generate(PROMPT, case_id="ecg", input="ЭКГ?", context={})
    assert "SECRET" not in str(raised.value) and "ecg" in str(raised.value)


def test_dspy_client_requires_credentials_before_any_call(monkeypatch):
    monkeypatch.delenv("UNSET_TEST_KEY", raising=False)
    with pytest.raises(MissingCredentialsError):
        DSPyLLMClient("openai/test-model", "UNSET_TEST_KEY").generate(PROMPT, case_id="a", input="?", context={})


@pytest.mark.parametrize("kwargs", [dict(model="no-provider"), dict(credential_env="not a name"),
                                    dict(max_tokens=0), dict(num_retries=-1), dict(temperature=3)])
def test_dspy_client_rejects_invalid_configuration(kwargs):
    with pytest.raises(GEPAConfigurationError):
        DSPyLLMClient(**{"model": "openai/m", "credential_env": "KEY", **kwargs})


def test_recording_replays_exactly_through_cli_responses(tmp_path):
    dataset = load("validation")
    scripted = FakeLLMClient({case.id: {"intent": "unknown", "asked": ["date"]} for case in dataset.cases})
    recorder = RecordingLLMClient(scripted)
    recorded = EvaluatePrompt(recorder, [DeterministicEvaluator()]).execute(PROMPT, dataset)
    path = tmp_path / "responses.json"
    recorder.save(path)
    replayed = EvaluatePrompt(_responses(str(path)), [DeterministicEvaluator()]).execute(PROMPT, dataset)
    assert replayed.scores == recorded.scores
    assert [c.output for c in replayed.cases] == [c.output for c in recorded.cases]


def test_recording_rejects_ambiguous_repeat():
    recorder = RecordingLLMClient(FakeLLMClient({"a": {"intent": "price"}}))
    recorder.generate(PROMPT, case_id="a", input="?", context={})
    with pytest.raises(ValueError, match="already recorded"):
        recorder.generate(PROMPT, case_id="a", input="?", context={})
