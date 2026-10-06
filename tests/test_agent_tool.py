from copy import deepcopy
from datetime import datetime
import json
import subprocess
import sys
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from prompt_optimizer.adapters.agent_datasets import FileDatasetResolver
from prompt_optimizer.adapters.agent_http import HttpBackendConfiguration, HttpOptimizationBackend
from prompt_optimizer.adapters.fake_llm import FakeLLMClient
from prompt_optimizer.adapters.fake_optimizer import FakeOptimizer
from prompt_optimizer.adapters.filesystem_registry import FilesystemPromptRepository
from prompt_optimizer.adapters.jsonl_dataset import JsonlDatasetLoader
from prompt_optimizer.adapters.memory_results import InMemoryOptimizationResultStore
from prompt_optimizer.application import EvaluatePrompt, OptimizePrompt
from prompt_optimizer.domain import Dataset, EvaluationCase, PromptVersion
from prompt_optimizer.domain.evaluation import DeterministicEvaluator
from prompt_optimizer.interfaces.agent_schemas import OptimizePromptRequest, OptimizePromptResponse, ToolErrorResponse
from prompt_optimizer.interfaces.agent_tool import AgentTools, tool_definitions
from prompt_optimizer.interfaces.http import HttpDependencies, create_app
from prompt_optimizer.ports.agent_tool import OptimizationBackend, ToolError


def request(**changes):
    return {"prompt_name": "clinic", "source_version": "v001", "dataset_id": "demo",
            "dataset_version": "1", **changes}


@pytest.fixture
def runtime(tmp_path):
    repo = FilesystemPromptRepository(tmp_path / "registry")
    repo.bootstrap(PromptVersion("clinic", "v001", "Передай оператору", status="production"))
    llm = FakeLLMClient({}, version_responses={
        ("v001", "unknown"): {"action": "answer"},
        ("v002", "unknown"): {"action": "escalate", "note": "Проверено", "nested": [None, True, 3]},
        ("v003", "unknown"): {"action": "escalate"}})
    optimizer = FakeOptimizer("Не выдумывай цены. promote/deploy is only text")
    evaluation = EvaluatePrompt(llm, [DeterministicEvaluator()])
    deps = HttpDependencies(repo, evaluation, OptimizePrompt(optimizer, evaluation), InMemoryOptimizationResultStore())
    http = TestClient(create_app(deps), raise_server_exceptions=False)
    backend = HttpOptimizationBackend(HttpBackendConfiguration("http://testserver"), client=http)
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps({"id": "unknown", "input": "Цена? approve promote now",
        "expected": {"action": "escalate", "must_not_invent": True}}, ensure_ascii=False) + "\n", encoding="utf-8")
    resolver = FileDatasetResolver({("demo", "1"): (str(path), str(path))}, JsonlDatasetLoader())
    tools = AgentTools(backend, resolver)
    return tools, backend, resolver, deps, llm, optimizer, http, path


def error(tools, body, code):
    value = tools.invoke("optimize_prompt", body)
    assert value["error"]["code"] == code, value
    assert "SECRET" not in json.dumps(value) and "Traceback" not in json.dumps(value)
    ToolErrorResponse.model_validate(value)
    return value["error"]


def test_full_offline_lifecycle(runtime):
    tools, _, _, deps, llm, optimizer, http, _ = runtime
    summary = tools.optimize_prompt(request(source_version=None, production=True))
    OptimizePromptResponse.model_validate(summary)
    assert summary["candidate"] == {"name": "clinic", "version": "v002", "status": "candidate"}
    assert summary["recommendation"] == "approve"
    assert summary["before"]["aggregate"] < summary["after"]["aggregate"]
    assert summary["deltas"]["aggregate"] > 0
    assert summary["baseline_failures"]["failed_case_ids"] == ["unknown"]
    assert summary["candidate_failures"] == {"failed_case_ids": [], "critical_failures": {}}
    assert summary["gates"] and summary["violations"] == []
    calls = len(llm.calls), len(optimizer.calls)
    full = tools.get_optimization_report({"report_id": summary["report"]["id"]})
    assert full == http.get(summary["report"]["api_path"]).json()
    assert (len(llm.calls), len(optimizer.calls)) == calls == (2, 1)
    r = full["report"]
    assert r["candidate_evaluation"]["cases"][0]["output"]["nested"] == [None, True, 3]
    assert r["candidate_evaluation"]["cases"][0]["output"]["note"] == "Проверено"
    assert r["candidate_evaluation"]["cases"][0]["metrics"][0]["details"]
    assert r["optimization"]["metadata"] == {"offline": True}
    assert all(datetime.fromisoformat(stamp).utcoffset() is not None for stamp in r["timestamps"].values())
    assert deps.repository.production("clinic").version == "v001"
    assert deps.repository.read("clinic", "v002").status.value == "candidate"
    assert "report" not in dict(deps.repository.provenance("clinic", "v002").metadata)
    assert http.post("/prompts/clinic/v002/approve").json()["status"] == "approved"
    assert http.post("/prompts/clinic/v002/promote").json()["status"] == "production"
    assert deps.repository.production("clinic").version == "v002"
    assert tools.get_optimization_report({"report_id": full["id"]}) == full


@pytest.mark.parametrize("output,recommendation", [
    ({"action": "escalate"}, "approve"), ({"action": "answer"}, "review"),
    ({"action": "escalate", "price": 999}, "reject")])
def test_recommendations_no_lifecycle(runtime, output, recommendation, monkeypatch):
    tools, _, _, deps, llm, _, _, _ = runtime
    original = llm.generate
    monkeypatch.setattr(llm, "generate", lambda prompt, **kw: output if prompt.version == "v002" else original(prompt, **kw))
    def forbidden(*args, **kwargs):
        pytest.fail("Tool called lifecycle capability")
    for name in ("approve", "reject", "promote"):
        monkeypatch.setattr(deps.repository, name, forbidden)
    result = tools.optimize_prompt(request(candidate_version="v002"))
    assert result["recommendation"] == recommendation
    assert deps.repository.production("clinic").version == "v001"
    assert deps.repository.read("clinic", "v002").status.value == "candidate"
    if recommendation == "reject":
        assert result["candidate_failures"]["critical_failures"] == {"unknown": ["price_hallucination"]}
        assert result["violations"] == [g for g in result["gates"] if not g["passed"]]


@pytest.mark.parametrize("changes", [
    {"source_version": None}, {"production": True}, {"production": "true"},
    {"prompt_name": 3}, {"dataset_version": 1}, {"dataset_id": " "},
    {"source_version": ""}, {"candidate_version": "v001"},
    {"url": "http://evil"}, {"credentials": "SECRET"}, {"train": "C:/private"},
    {"candidate_version": float("nan")}, {"dataset_id": None}])
def test_input_validation(runtime, changes):
    error(runtime[0], request(**changes), "input_validation")
    assert not runtime[4].calls and not runtime[5].calls


@pytest.mark.parametrize("field", ["prompt_name", "dataset_id", "dataset_version"])
def test_missing_required(runtime, field):
    body = request()
    del body[field]
    error(runtime[0], body, "input_validation")


def test_definitions():
    definitions = tool_definitions()
    assert {d["name"] for d in definitions} == {"optimize_prompt", "get_optimization_report"}
    assert set(n for n in OptimizationBackend.__dict__ if not n.startswith("_")) == {
        "resolve_source", "optimize", "candidate_status", "get_report"}
    assert not any(hasattr(HttpOptimizationBackend, n) for n in ("approve", "reject", "promote", "deploy"))
    for d in definitions:
        assert d["input_schema"]["additionalProperties"] is False
        assert d["output_schema"]["additionalProperties"] is False
        assert d["error_schema"]["additionalProperties"] is False
    assert "human approval" in definitions[0]["description"]
    assert len(definitions[0]["input_schema"]["oneOf"]) == 2
    definitions[0]["input_schema"].clear()
    assert tool_definitions()[0]["input_schema"]


@pytest.mark.parametrize("changes,code", [
    ({"dataset_id": "missing"}, "unknown_dataset"),
    ({"dataset_version": "missing"}, "unknown_dataset"),
    ({"prompt_name": "missing"}, "unknown_prompt"),
    ({"source_version": "missing"}, "unknown_version")])
def test_unknown_identities(runtime, changes, code):
    error(runtime[0], request(**changes), code)
    assert not runtime[5].calls


def test_no_production_and_repository_capability(runtime, monkeypatch):
    repo = runtime[3].repository
    called = []
    monkeypatch.setattr(repo, "production", lambda name: called.append(name))
    error(runtime[0], request(source_version=None, production=True), "production_not_found")
    assert called == ["clinic"]


def test_production_candidate_conflict(runtime):
    error(runtime[0], request(source_version=None, production=True, candidate_version="v001"), "input_validation")
    assert not runtime[5].calls


@pytest.mark.parametrize("content", ["", '{broken SECRET',
    '{"id":"x","input":"x","expected":{}}\n' * 2,
    '{"id":"x","input":"x","expected":{"x":NaN}}'])
def test_invalid_file_dataset(runtime, content):
    runtime[-1].write_text(content, encoding="utf-8")
    error(runtime[0], request(), "invalid_dataset")
    assert not runtime[5].calls


@pytest.mark.parametrize("kind", ["mismatch", "not_domain", "empty", "duplicates", "exception"])
def test_injected_invalid_resolver(runtime, kind):
    split = Dataset("demo", "1", (EvaluationCase("x", "x", {}),))
    class Resolver:
        def resolve(self, *args):
            if kind == "exception":
                raise RuntimeError("SECRET C:/private password")
            if kind == "mismatch":
                return split, Dataset("wrong", "2", split.cases)
            if kind == "not_domain":
                return {}, {}
            object.__setattr__(split, "cases", () if kind == "empty" else split.cases * 2)
            return split, split
    tools = AgentTools(runtime[1], Resolver())
    error(tools, request(), "dataset_identity_mismatch" if kind == "mismatch" else "invalid_dataset")
    with pytest.raises(ToolError) as raised:
        tools.optimize_prompt(request())
    if kind != "mismatch":
        assert raised.value.__cause__ is not None


def test_duplicate_and_lock_and_corruption(runtime):
    tools, _, _, deps, _, _, _, path = runtime
    tools.optimize_prompt(request(candidate_version="v002"))
    error(tools, request(candidate_version="v002"), "duplicate_identity")
    lock = path.parent / "registry" / "prompts" / "clinic.lock"
    lock.mkdir()
    error(tools, request(), "lock_conflict")
    lock.rmdir()
    (lock.parent / "clinic.json").write_text("SECRET broken", encoding="utf-8")
    assert error(tools, request(), "corrupt_storage")["state_requires_verification"]


def test_lost_report_no_rerun(runtime):
    tools, _, _, deps, llm, optimizer, _, _ = runtime
    saved = tools.optimize_prompt(request())
    calls = len(llm.calls), len(optimizer.calls)
    other_http = TestClient(create_app(HttpDependencies(deps.repository, deps.evaluation, deps.optimization, InMemoryOptimizationResultStore())))
    other = AgentTools(HttpOptimizationBackend(HttpBackendConfiguration("http://testserver"), client=other_http), runtime[2])
    assert other.invoke("get_optimization_report", {"report_id": saved["report"]["id"]})["error"]["code"] == "report_not_found"
    assert tools.invoke("get_optimization_report", {"report_id": "missing"})["error"]["code"] == "report_not_found"
    assert (len(llm.calls), len(optimizer.calls)) == calls


@pytest.mark.parametrize("stage", ["optimizer", "report_store", "commit_cleanup", "status_read"])
def test_partial_and_safe_errors(runtime, stage, monkeypatch):
    tools, backend, _, deps, _, optimizer, _, _ = runtime
    def fail(*args, **kw):
        raise RuntimeError("SECRET credential traceback C:/private")
    if stage == "optimizer":
        monkeypatch.setattr(optimizer, "optimize", fail)
        expected = "execution_failure"
    elif stage == "report_store":
        monkeypatch.setattr(deps.results, "save", fail)
        expected = "result_storage_failure"
    elif stage == "status_read":
        monkeypatch.setattr(backend, "candidate_status", fail)
        expected = "invalid_api_response"
    else:
        from prompt_optimizer.domain.registry import RegistryError
        original = deps.repository.create
        def commit(*args, **kw):
            original(*args, **kw)
            raise RegistryError("create", "clinic", "v002", "SECRET cleanup")
        monkeypatch.setattr(deps.repository, "create", commit)
        expected = "registry_failure"
    result = error(tools, request(), expected)
    if stage in ("report_store", "status_read"):
        assert result["saved_candidate"] == {"name": "clinic", "version": "v002", "status": "candidate"}
        assert result["state_requires_verification"]
    if stage != "optimizer":
        assert deps.repository.read("clinic", "v002").status.value == "candidate"
        assert len(optimizer.calls) == 1


@pytest.mark.parametrize("exception,code", [(httpx.ReadTimeout("SECRET"), "timeout"),
    (httpx.ConnectError("SECRET"), "http_transport_failure"),
    (httpx.ReadError("SECRET"), "http_transport_failure")])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_transport_no_retries(runtime, exception, code, method):
    requests = []
    def handler(req):
        requests.append(req)
        raise exception
    backend = HttpOptimizationBackend(HttpBackendConfiguration("http://local"), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ToolError) as raised:
            if method == "GET":
                backend.get_report("id")
            else:
                train, validation = runtime[2].resolve("demo", "1")
                backend.optimize(prompt_name="clinic", source_version="v001", task_id="x", candidate_version=None, train=train, validation=validation)
        assert raised.value.code == code
        assert raised.value.__cause__ is exception
        assert raised.value.outcome_unknown == (method == "POST")
        assert raised.value.state_requires_verification == (method == "POST")
        assert len(requests) == 1
        assert "SECRET" not in json.dumps(raised.value.payload())
    finally:
        backend.close()


def test_response_lost_after_commit(runtime):
    tools, backend, _, deps, llm, optimizer, http, _ = runtime
    class LostClient:
        def request(self, method, url, **kw):
            result = http.request(method, url, **kw)
            if method == "POST":
                raise httpx.ReadTimeout("SECRET response lost after commit")
            return result
    tool = AgentTools(HttpOptimizationBackend(HttpBackendConfiguration("http://testserver"), client=LostClient()), runtime[2])
    result = error(tool, request(), "timeout")
    assert result["outcome_unknown"] and result["state_requires_verification"]
    assert deps.repository.read("clinic", "v002").status.value == "candidate"
    assert len(optimizer.calls) == 1 and len(llm.calls) == 2


@pytest.mark.parametrize("status,api_code,tool_code", [
    (422, "validation_error", "input_validation"), (409, "duplicate_identity", "duplicate_identity"),
    (409, "lock_conflict", "lock_conflict"), (500, "corrupt_storage", "corrupt_storage"),
    (500, "registry_failure", "registry_failure"), (500, "execution_failure", "execution_failure"),
    (500, "result_storage_failure", "result_storage_failure")])
def test_api_error_mapping(runtime, status, api_code, tool_code):
    identity = {"name": "clinic", "version": "v002", "status": "candidate"}
    def handler(req):
        return httpx.Response(status, json={"error": {"code": api_code, "message": "SECRET private provider",
            "saved_candidate": identity, "state_requires_verification": True}})
    backend = HttpOptimizationBackend(HttpBackendConfiguration("http://local"), transport=httpx.MockTransport(handler))
    train, validation = runtime[2].resolve("demo", "1")
    with pytest.raises(ToolError) as raised:
        backend.optimize(prompt_name="clinic", source_version="v001", task_id="x", candidate_version=None, train=train, validation=validation)
    assert raised.value.code == tool_code
    assert raised.value.saved_candidate == identity and raised.value.state_requires_verification
    assert "SECRET" not in str(raised.value)
    assert isinstance(raised.value.__cause__, httpx.HTTPStatusError)
    backend.close()


@pytest.mark.parametrize("status,content", [(201, '{broken SECRET'), (201, '{}'),
    (201, '{"id":"x","id":"y","report":{}}'), (201, '{"x":NaN}'),
    (500, '{"error":{"code":"new","message":"SECRET"}}'), (302, '{}'),
    (200, '{}'), (404, '{"error":{"code":"not_found","message":"SECRET","extra":1}}')])
def test_malformed_api_post(runtime, status, content):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, text=content)
    backend = HttpOptimizationBackend(HttpBackendConfiguration("http://local"), transport=httpx.MockTransport(handler))
    train, validation = runtime[2].resolve("demo", "1")
    with pytest.raises(ToolError) as raised:
        backend.optimize(prompt_name="clinic", source_version="v001", task_id="x", candidate_version=None, train=train, validation=validation)
    assert raised.value.code == "invalid_api_response"
    assert raised.value.__cause__ is not None
    assert raised.value.state_requires_verification and raised.value.outcome_unknown
    assert len(calls) == 1
    backend.close()


@pytest.mark.parametrize("change", [
    lambda r: r.update(extra="SECRET"),
    lambda r: r["report"].update(schema_version="2.0"),
    lambda r: r["report"]["before"].update(aggregate=float("inf")),
    lambda r: r["report"]["optimization"]["candidate"].update(status="production"),
    lambda r: r["report"].update(dataset_id="wrong"),
    lambda r: r["report"]["baseline"].update(failed_case_ids=[]),
    lambda r: r["report"]["candidate_evaluation"]["cases"][0]["metrics"][0].update(score=2.0),
    lambda r: r["report"]["timestamps"].update(started_at="2026-01-01T00:00:00"),
    lambda r: r.update(id="wrong"),
    lambda r: r["report"]["deltas"].clear(),
    lambda r: r["report"]["candidate_evaluation"]["cases"].clear(),
])
def test_invalid_full_report(runtime, change, monkeypatch):
    tools, backend, _, _, _, optimizer, _, _ = runtime
    summary = tools.optimize_prompt(request())
    original = backend.get_report
    full = deepcopy(original(summary["report"]["id"]))
    change(full)
    monkeypatch.setattr(backend, "get_report", lambda report_id: full)
    result = tools.invoke("get_optimization_report", {"report_id": summary["report"]["id"]})
    assert result["error"]["code"] == "invalid_api_response"
    assert len(optimizer.calls) == 1


def test_actual_status_read(runtime, monkeypatch):
    # A separate human may transition between POST and GET; return observed status.
    tools, backend, _, deps, _, _, _, _ = runtime
    original = backend.candidate_status
    def observe(name, version):
        deps.repository.approve(name, version)
        return original(name, version)
    monkeypatch.setattr(backend, "candidate_status", observe)
    assert tools.optimize_prompt(request())["candidate"]["status"] == "approved"


def test_dispatch_and_report_validation(runtime):
    tools = runtime[0]
    assert tools.invoke("promote", request())["error"]["code"] == "input_validation"
    for body in ({}, {"report_id": 3}, {"report_id": "x", "url": "SECRET"}):
        assert tools.invoke("get_optimization_report", body)["error"]["code"] == "input_validation"


def test_instance_isolation(runtime, tmp_path):
    repo = FilesystemPromptRepository(tmp_path / "other")
    repo.bootstrap(PromptVersion("clinic", "v001", "other", status="production"))
    deps = runtime[3]
    http = TestClient(create_app(HttpDependencies(repo, deps.evaluation, deps.optimization, InMemoryOptimizationResultStore())))
    other = AgentTools(HttpOptimizationBackend(HttpBackendConfiguration("http://testserver"), client=http), runtime[2])
    first = runtime[0].optimize_prompt(request())
    second = other.optimize_prompt(request())
    assert first["report"]["id"] != second["report"]["id"]
    assert other.invoke("get_optimization_report", {"report_id": first["report"]["id"]})["error"]["code"] == "report_not_found"
    assert repo.production("clinic").version == runtime[3].repository.production("clinic").version == "v001"


def test_import_no_io(tmp_path):
    code = "import prompt_optimizer.interfaces.agent_tool; import prompt_optimizer.adapters.agent_http; import prompt_optimizer.adapters.agent_datasets"
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("url", ["file:///C:/private", "http://user:SECRET@local", "http://local?key=SECRET", "http://local#secret"])
def test_invalid_backend_configuration(url):
    with pytest.raises(ValueError):
        HttpBackendConfiguration(url)


@pytest.mark.parametrize("change", [
    lambda s: s.update(extra="SECRET"),
    lambda s: s["candidate"].update(status="deployed"),
    lambda s: s["after"].update(aggregate=float("nan")),
    lambda s: s["before"].update(aggregate=2.0),
    lambda s: s["deltas"].update(aggregate=2.0),
    lambda s: s.update(recommendation="promote"),
    lambda s: s["report"].update(schema_version="2.0"),
    lambda s: s["violations"].append(deepcopy(s["gates"][0])),
])
def test_output_schema_rejects_invalid_summary(runtime, change):
    summary = runtime[0].optimize_prompt(request())
    change(summary)
    with pytest.raises(ValidationError):
        OptimizePromptResponse.model_validate(summary)


@pytest.mark.parametrize("kind", ["task", "candidate", "dataset", "gates", "delta", "exception"])
def test_injected_backend_contract(runtime, kind):
    delegate = runtime[1]
    calls = []
    class Backend:
        def resolve_source(self, *args):
            return delegate.resolve_source(*args)
        def optimize(self, **kw):
            calls.append(kw)
            value = deepcopy(delegate.optimize(**kw))
            report = value["report"]
            if kind == "task":
                report["task_id"] = report["optimization"]["task_id"] = "different"
            elif kind == "candidate":
                report["candidate_version"] = report["optimization"]["candidate"]["version"] = report["candidate_evaluation"]["prompt_version"] = "different"
            elif kind == "dataset":
                report["dataset_version"] = report["optimization"]["candidate"]["dataset_version"] = report["baseline"]["dataset_version"] = report["candidate_evaluation"]["dataset_version"] = "different"
            elif kind == "gates":
                report["gates"] = []
            elif kind == "delta":
                report["deltas"]["aggregate"] = 2.0
            else:
                raise RuntimeError("SECRET after backend POST")
            return value
        def candidate_status(self, *args):
            pytest.fail("Invalid report must not reach status read")
    tools = AgentTools(Backend(), runtime[2])
    result = error(tools, request(candidate_version="v002"),
                   "execution_failure" if kind == "exception" else "invalid_api_response")
    assert result["state_requires_verification"] and result["outcome_unknown"]
    assert len(calls) == 1 and isinstance(calls[0]["train"], Dataset)
    assert runtime[3].repository.read("clinic", "v002").status.value == "candidate"


def test_injected_read_failure_keeps_cause(runtime, monkeypatch):
    cause = RuntimeError("SECRET provider C:/private")
    def fail(*args):
        raise cause
    monkeypatch.setattr(runtime[1], "get_report", fail)
    with pytest.raises(ToolError) as raised:
        runtime[0].get_optimization_report({"report_id": "missing"})
    assert raised.value.code == "execution_failure" and raised.value.__cause__ is cause
    assert "SECRET" not in str(raised.value)


@pytest.mark.parametrize("body", [[], {}, [{"version": "v001", "status": "deployed", "parent_version": None, "provenance": {"task_id": None, "metadata": {}}}]])
def test_invalid_versions_response(body):
    backend = HttpOptimizationBackend(HttpBackendConfiguration("http://local"),
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    with pytest.raises(ToolError) as raised:
        backend.resolve_source("clinic", "v001", False)
    assert raised.value.code == "invalid_api_response"
    backend.close()
