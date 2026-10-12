"""Real-service composition, persistent reports and the MCP bridge; no network."""
import json
import subprocess
import sys
from uuid import uuid4

import anyio
import dspy
from dspy.utils import DummyLM
from fastapi.testclient import TestClient
import pytest

from prompt_optimizer.adapters.dspy.gepa_optimizer import (GEPAConfig, GEPAConfigurationError,
    MissingCredentialsError, structured_signature)
from prompt_optimizer.adapters.dspy.llm_client import DSPyLLMClient
from prompt_optimizer.adapters.file_results import FileOptimizationResultStore
from prompt_optimizer.adapters.filesystem_registry import FilesystemPromptRepository
from prompt_optimizer.domain import PromptVersion
from prompt_optimizer.http_runtime import (NEUTRAL_OUTPUT_DESCRIPTION, OfflineConfiguration, RealConfiguration,
    build_offline_app, build_real_app)
from prompt_optimizer.interfaces.agent_tool import tool_definitions
from prompt_optimizer.interfaces.mcp_server import McpConfiguration, build_tools, call_tool, list_tools

ENV = {"PROMPT_REGISTRY_ROOT": "/srv/registry", "GEPA_MODEL": "openai/a", "GEPA_REFLECTION_MODEL": "openai/b"}


# --- file result store -------------------------------------------------------

def test_file_store_persists_across_instances(tmp_path):
    identity = str(uuid4())
    FileOptimizationResultStore(tmp_path).save(identity, {"id": identity, "score": 0.5, "text": "Цена"})
    assert FileOptimizationResultStore(tmp_path).get(identity) == {"id": identity, "score": 0.5, "text": "Цена"}
    assert [p.name for p in tmp_path.iterdir()] == [f"{identity}.json"]  # No temp files left.


def test_file_store_rejects_duplicates_and_never_overwrites(tmp_path):
    store, identity = FileOptimizationResultStore(tmp_path), str(uuid4())
    store.save(identity, {"v": 1})
    with pytest.raises(ValueError, match="Duplicate"):
        store.save(identity, {"v": 2})
    assert store.get(identity) == {"v": 1}
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("identity", ["../escape", "x.json", "", str(uuid4()).upper(), str(uuid4()) + "/"])
def test_file_store_only_maps_uuid_identities(tmp_path, identity):
    store = FileOptimizationResultStore(tmp_path / "reports")
    assert store.get(identity) is None
    with pytest.raises(ValueError, match="UUID"):
        store.save(identity, {})
    assert not (tmp_path / "reports").exists()


# --- real service composition ------------------------------------------------

def test_real_configuration_from_env_defaults():
    config = RealConfiguration.from_env(ENV)
    assert (config.reports_root, config.credential_env, config.max_tokens, config.max_metric_calls) == (
        "/srv/registry-reports", "OPENAI_API_KEY", 16000, 150)
    assert config.output_description == NEUTRAL_OUTPUT_DESCRIPTION


@pytest.mark.parametrize("env,match", [({}, "PROMPT_REGISTRY_ROOT"), ({**ENV, "GEPA_MODEL": " "}, "GEPA_MODEL"),
                                       ({**ENV, "GEPA_MAX_TOKENS": "many"}, "integers")])
def test_real_configuration_rejects_incomplete_env(env, match):
    with pytest.raises(ValueError, match=match):
        RealConfiguration.from_env(env)


def test_real_app_requires_credentials_at_startup(tmp_path, monkeypatch):
    monkeypatch.delenv("UNSET_TEST_KEY", raising=False)
    config = RealConfiguration.from_env({**ENV, "PROMPT_REGISTRY_ROOT": str(tmp_path / "registry"),
                                         "PROMPT_OPT_CREDENTIAL_ENV": "UNSET_TEST_KEY"})
    with pytest.raises(MissingCredentialsError):
        build_real_app(config)


def test_real_app_builds_without_network_and_persists_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSET_TEST_KEY", "sk-test")
    config = RealConfiguration.from_env({**ENV, "PROMPT_REGISTRY_ROOT": str(tmp_path / "registry"),
                                         "PROMPT_OPT_CREDENTIAL_ENV": "UNSET_TEST_KEY"})
    deps = build_real_app(config).state.dependencies
    assert isinstance(deps.results, FileOptimizationResultStore)
    assert str(deps.results.root) == str(tmp_path / "registry-reports")
    assert deps.optimization.optimizer.config.output_description == NEUTRAL_OUTPUT_DESCRIPTION
    assert not (tmp_path / "registry").exists()  # Nothing is created before the first request.


def test_runtime_import_does_not_load_dspy():
    code = "import sys, prompt_optimizer.http_runtime; assert 'dspy' not in sys.modules"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0


# --- output description ------------------------------------------------------

def test_default_signature_keeps_clinic_hint_and_override_applies():
    default = structured_signature("X").output_fields["output"].json_schema_extra["desc"]
    assert default.startswith("Structured response: intent, price")
    custom = structured_signature("X", "Return {label}").output_fields["output"].json_schema_extra["desc"]
    assert custom == "Return {label}"


def test_llm_client_sends_custom_output_description():
    lm = DummyLM([{"output": {"label": "spam"}}], adapter=dspy.JSONAdapter())
    client = DSPyLLMClient("openai/m", "KEY", output_description="Spam classifier JSON", lm=lm)
    assert client.generate(PromptVersion("mail", "v001", "Classify"), case_id="a", input="hi", context={}) == {
        "label": "spam"}
    assert "Spam classifier JSON" in lm.history[-1]["messages"][0]["content"]


@pytest.mark.parametrize("value", ["", "   ", 5])
def test_blank_output_description_rejected(value):
    with pytest.raises(GEPAConfigurationError):
        GEPAConfig("openai/a", "openai/b", "KEY", output_description=value)
    with pytest.raises(GEPAConfigurationError):
        DSPyLLMClient("openai/a", "KEY", output_description=value)


# --- MCP bridge --------------------------------------------------------------

class InProcessClient:
    """TestClient ignores per-request timeouts and warns; the adapter always sends one."""
    def __init__(self, client):
        self._client = client

    def request(self, method, url, *, timeout=None, **kw):
        return self._client.request(method, url, **kw)


def write_config(tmp_path, datasets=None):
    case = {"id": "unknown", "input": "Неизвестная цена?", "expected": {"action": "escalate", "must_not_invent": True}}
    (tmp_path / "data").mkdir()
    for split in ("train", "validation"):
        (tmp_path / "data" / f"{split}.jsonl").write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"base_url": "http://testserver", "datasets": datasets or {
        "demo@1": {"train": "data/train.jsonl", "validation": "data/validation.jsonl"}}}), encoding="utf-8")
    return path


def test_mcp_config_resolves_paths_relative_to_file(tmp_path):
    config = McpConfiguration.load(write_config(tmp_path))
    assert config.timeout_seconds == 1800
    assert config.datasets == {("demo", "1"): (str(tmp_path / "data" / "train.jsonl"),
                                               str(tmp_path / "data" / "validation.jsonl"))}


def test_mcp_config_rejects_bad_dataset_key(tmp_path):
    with pytest.raises(ValueError, match="id@version"):
        McpConfiguration.load(write_config(tmp_path, {"demo": {"train": "a", "validation": "b"}}))


def test_mcp_lists_exactly_agent_tools_without_lifecycle():
    tools = list_tools().tools
    assert [(t.name, t.input_schema) for t in tools] == [(d["name"], d["input_schema"]) for d in tool_definitions()]
    assert not {t.name for t in tools} & {"approve", "promote", "reject", "approve_prompt", "promote_prompt"}


def test_mcp_call_tool_optimizes_and_reports_errors(tmp_path):
    repository = FilesystemPromptRepository(tmp_path / "registry")
    repository.bootstrap(PromptVersion("clinic", "v001", "Передай оператору", status="production"))
    app = build_offline_app(OfflineConfiguration(str(tmp_path / "registry"), "Не выдумывай цены", {
        ("v001", "unknown"): {"action": "answer"}, ("v002", "unknown"): {"action": "escalate"}}))
    tools = build_tools(McpConfiguration.load(write_config(tmp_path)), client=InProcessClient(TestClient(app)))
    request = {"prompt_name": "clinic", "source_version": "v001", "dataset_id": "demo", "dataset_version": "1"}

    result = anyio.run(call_tool, tools, "optimize_prompt", request)
    assert not result.is_error
    assert result.structured_content["candidate"] == {"name": "clinic", "version": "v002", "status": "candidate"}
    assert json.loads(result.content[0].text) == result.structured_content
    assert repository.production("clinic").version == "v001"

    missing = anyio.run(call_tool, tools, "optimize_prompt", {**request, "dataset_id": "absent"})
    assert missing.is_error and missing.structured_content["error"]["code"] == "unknown_dataset"
    unknown = anyio.run(call_tool, tools, "promote", {"prompt_name": "clinic"})
    assert unknown.is_error and unknown.structured_content["error"]["code"] == "input_validation"
