from copy import deepcopy
from datetime import datetime
import json
import subprocess
import sys
from threading import get_ident
import pytest
from fastapi.testclient import TestClient
from prompt_optimizer.adapters.fake_llm import FakeLLMClient
from prompt_optimizer.adapters.fake_optimizer import FakeOptimizer
from prompt_optimizer.adapters.filesystem_registry import FilesystemPromptRepository
from prompt_optimizer.adapters.memory_results import InMemoryOptimizationResultStore
from prompt_optimizer.application import EvaluatePrompt, OptimizePrompt
from prompt_optimizer.domain import PromptVersion
from prompt_optimizer.domain.evaluation import DeterministicEvaluator
from prompt_optimizer.domain.registry import RegistryError
from prompt_optimizer.interfaces.http import HttpDependencies, create_app, get_dependencies
from prompt_optimizer.interfaces.http_schemas import OptimizationResponse


def dataset():
    return {"id": "demo", "version": "1", "cases": [{"id": "unknown", "input": "Цена?",
        "expected": {"action": "escalate", "must_not_invent": True}}]}


def optimize_body():
    return {"prompt_name": "clinic", "prompt_version": "v001", "task_id": "http-08",
            "train": dataset(), "validation": dataset()}


@pytest.fixture
def runtime(tmp_path):
    repo = FilesystemPromptRepository(tmp_path / "registry")
    repo.bootstrap(PromptVersion("clinic", "v001", "Передавай оператору", status="production"))
    llm = FakeLLMClient({}, version_responses={
        ("v001", "unknown"): {"action": "answer"},
        ("v002", "unknown"): {"action": "escalate", "note": "Проверено", "nested": [None, 3, True]},
        ("v003", "unknown"): {"action": "escalate"}})
    optimizer = FakeOptimizer("Не выдумывай цены")
    evaluation = EvaluatePrompt(llm, [DeterministicEvaluator()])
    deps = HttpDependencies(repo, evaluation, OptimizePrompt(optimizer, evaluation), InMemoryOptimizationResultStore())
    return deps, llm, optimizer, tmp_path


def client(runtime):
    return TestClient(create_app(runtime[0]))


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert set(response.json()) == {"error"}
    error = response.json()["error"]
    assert set(error) == {"code", "message", "saved_candidate", "state_requires_verification"}
    assert error["code"] == code
    assert "SECRET" not in response.text and "Traceback" not in response.text


def test_full_offline_lifecycle(runtime):
    deps, llm, optimizer, _ = runtime
    http = client(runtime)
    evaluation = http.post("/evaluations", json={"prompt_name": "clinic", "prompt_version": "v001", "dataset": dataset()})
    assert evaluation.status_code == 200
    detail = evaluation.json()["evaluation"]
    assert detail["failed_case_ids"] == ["unknown"]
    assert detail["prompt_name"] == "clinic" and detail["dataset_version"] == "1"
    assert detail["cases"][0]["output"] == {"action": "answer"}
    assert detail["cases"][0]["metrics"][0]["details"]
    assert detail["critical_failures"] == {}
    response = http.post("/optimizations", json=optimize_body())
    assert response.status_code == 201, response.text
    data = response.json()
    report = data["report"]
    assert report["recommendation"] == "approve"
    assert report["schema_version"] == "1.0"
    assert report["before"]["aggregate"] < report["after"]["aggregate"]
    assert report["deltas"]["aggregate"] > 0 and report["gates"]
    assert report["optimization"]["metadata"] == {"offline": True}
    assert report["task_id"] == "http-08"
    assert report["candidate_evaluation"]["cases"][0]["output"]["nested"] == [None, 3, True]
    assert "Проверено" in response.text
    for stamp in (*report["timestamps"].values(), report["optimization"]["candidate"]["created_at"]):
        assert datetime.fromisoformat(stamp).utcoffset() is not None
    assert datetime.fromisoformat(report["timestamps"]["started_at"]) <= datetime.fromisoformat(report["timestamps"]["completed_at"])
    assert deps.repository.read("clinic", "v002").status.value == "candidate"
    assert dict(deps.repository.read("clinic", "v002").scores) == report["after"]
    assert deps.repository.production("clinic").version == "v001"
    calls = len(llm.calls), len(optimizer.calls)
    assert http.get(f"/optimizations/{data['id']}").json() == data
    assert (len(llm.calls), len(optimizer.calls)) == calls
    versions = http.get("/prompts/clinic/versions")
    assert versions.status_code == 200
    assert versions.json()[1] == {"version": "v002", "status": "candidate", "parent_version": "v001",
        "provenance": {"task_id": "http-08", "metadata": {"offline": True}}}
    assert_error(http.post("/prompts/clinic/v002/promote"), 409, "invalid_transition")
    assert http.post("/prompts/clinic/v002/approve").json()["status"] == "approved"
    assert http.post("/prompts/clinic/v002/promote").json()["status"] == "production"
    assert deps.repository.read("clinic", "v001").status.value == "archived"
    assert deps.repository.production("clinic").version == "v002"
    # The saved run is historical and stays byte-for-byte equivalent after promotion.
    assert http.get(f"/optimizations/{data['id']}").json() == data
    body = optimize_body()
    body["candidate_version"] = "v003"
    assert http.post("/optimizations", json=body).status_code == 201
    assert http.post("/prompts/clinic/v003/reject").json()["status"] == "rejected"
    assert_error(http.post("/prompts/clinic/v003/reject"), 409, "invalid_transition")


@pytest.mark.parametrize("output,recommendation", [({"action": "answer"}, "review"),
    ({"action": "escalate", "price": 999}, "reject")])
def test_recommendations_are_success_not_transitions(runtime, output, recommendation):
    deps, _, _, _ = runtime
    llm = FakeLLMClient({}, version_responses={( "v001", "unknown"): {"action": "answer"}, ("v002", "unknown"): output})
    evaluation = EvaluatePrompt(llm, [DeterministicEvaluator()])
    http = TestClient(create_app(HttpDependencies(deps.repository, evaluation,
        OptimizePrompt(FakeOptimizer("text"), evaluation), deps.results)))
    response = http.post("/optimizations", json=optimize_body())
    assert response.status_code == 201
    report = response.json()["report"]
    assert report["recommendation"] == recommendation
    if recommendation == "reject":
        assert report["candidate_evaluation"]["critical_failures"]["unknown"] == ["price_hallucination"]
    assert deps.repository.read("clinic", "v002").status.value == "candidate"
    assert deps.repository.production("clinic").version == "v001"


@pytest.mark.parametrize("change", [
    lambda b: b.update(credentials="SECRET"),
    lambda b: b.update(prompt_name=123),
    lambda b: b.update(task_id=" "),
    lambda b: b.update(candidate_version="v001"),
    lambda b: b["train"].update(cases=[]),
    lambda b: b["train"].update(version="2"),
    lambda b: b["validation"].pop("id"),
    lambda b: b["train"]["cases"].append(deepcopy(b["train"]["cases"][0])),
    lambda b: b["train"]["cases"][0].update(extra="SECRET"),
    lambda b: b["train"]["cases"][0].update(tags=[2]),
    lambda b: b["train"]["cases"][0].update(expected=[]),
    lambda b: b["train"]["cases"][0]["expected"].update(nested={"bad": float("inf")}),
    lambda b: b["train"]["cases"][0].update(context={"nan": float("nan")}),
    lambda b: b["train"]["cases"][0].update(context={"nested": {"api_key": "SECRET"}}),
])
def test_validation(runtime, change):
    body = optimize_body()
    change(body)
    # stdlib JSON intentionally permits NaN/Infinity; HTTP boundary must reject them.
    response = client(runtime).post("/optimizations", content=json.dumps(body), headers={"content-type": "application/json"})
    assert_error(response, 422, "validation_error")
    assert not runtime[1].calls and not runtime[2].calls


def test_invalid_evaluation_and_malformed_json(runtime):
    http = client(runtime)
    assert_error(http.post("/evaluations", json={}), 422, "validation_error")
    assert_error(http.post("/optimizations", content='{broken SECRET'), 422, "validation_error")


def test_openapi(runtime):
    spec = client(runtime).get("/openapi.json").json()
    assert len(spec["paths"]) == 8
    schemas = spec["components"]["schemas"]
    assert schemas["OptimizationRequest"]["additionalProperties"] is False
    assert schemas["OptimizationResponse"]["properties"]["report"]["$ref"].endswith("/OptimizationReport")
    assert schemas["OptimizationReport"]["properties"]["schema_version"]["const"] == "1.0"
    for path, methods in spec["paths"].items():
        for method in methods.values():
            for status in ("404", "409", "422", "500"):
                assert method["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")


@pytest.mark.parametrize("path", ["/optimizations/missing", "/prompts/missing/versions", "/missing"])
def test_missing_resources(runtime, path):
    assert_error(client(runtime).get(path), 404, "not_found")


@pytest.mark.parametrize("action", ["approve", "reject", "promote"])
def test_missing_version(runtime, action):
    assert_error(client(runtime).post(f"/prompts/clinic/missing/{action}"), 404, "not_found")


def test_duplicate_version(runtime):
    http = client(runtime)
    body = optimize_body()
    body["candidate_version"] = "v002"
    assert http.post("/optimizations", json=body).status_code == 201
    assert_error(http.post("/optimizations", json=body), 409, "duplicate_identity")


def test_registry_lock_corruption(runtime):
    _, _, _, path = runtime
    http = client(runtime)
    lock = path / "registry" / "prompts" / "clinic.lock"
    lock.mkdir()
    assert_error(http.get("/prompts/clinic/versions"), 409, "lock_conflict")
    assert_error(http.post("/optimizations", json=optimize_body()), 409, "lock_conflict")
    lock.rmdir()
    (path / "registry" / "prompts" / "clinic.json").write_text("SECRET broken", encoding="utf-8")
    assert_error(http.get("/prompts/clinic/versions"), 500, "corrupt_storage")


@pytest.mark.parametrize("stage", ["evaluation", "optimizer"])
def test_pipeline_failure_safe_and_no_candidate(runtime, monkeypatch, stage):
    deps, llm, optimizer, _ = runtime
    def fail(*args, **kwargs):
        raise RuntimeError("SECRET provider token C:/private/path")
    monkeypatch.setattr(llm if stage == "evaluation" else optimizer, "generate" if stage == "evaluation" else "optimize", fail)
    response = client(runtime).post("/optimizations", json=optimize_body())
    assert_error(response, 500, "execution_failure")
    assert len(deps.repository.list("clinic")) == 1
    assert response.json()["error"]["saved_candidate"] is None
    if stage == "evaluation":
        assert_error(client(runtime).post("/evaluations", json={"prompt_name": "clinic", "prompt_version": "v001", "dataset": dataset()}), 500, "execution_failure")


@pytest.mark.parametrize("stage", ["report_build", "report_writer", "result_store"])
def test_partial_workflow(runtime, monkeypatch, stage):
    deps, _, optimizer, _ = runtime
    def fail(*args, **kwargs):
        raise OSError("SECRET disk C:/private/path")
    if stage == "report_build":
        monkeypatch.setattr("prompt_optimizer.interfaces.http.build_report", fail)
    elif stage == "report_writer":
        class BrokenWriter:
            write = fail
        deps = HttpDependencies(deps.repository, deps.evaluation, deps.optimization, deps.results,
                                BrokenWriter(), "server-selected.json")
    else:
        monkeypatch.setattr(deps.results, "save", fail)
    response = TestClient(create_app(deps)).post("/optimizations", json=optimize_body())
    assert_error(response, 500, "result_storage_failure")
    assert response.json()["error"]["saved_candidate"] == {"name": "clinic", "version": "v002", "status": "candidate"}
    assert deps.repository.read("clinic", "v002").status.value == "candidate"
    assert len(optimizer.calls) == 1


def test_post_commit_registry_error(runtime, monkeypatch):
    deps = runtime[0]
    original = deps.repository.create
    def commit_then_fail(prompt, **kwargs):
        original(prompt, **kwargs)
        raise RegistryError("create", prompt.name, prompt.version, "lock cleanup failed; commit may have succeeded") from OSError("SECRET")
    monkeypatch.setattr(deps.repository, "create", commit_then_fail)
    response = client(runtime).post("/optimizations", json=optimize_body())
    assert_error(response, 500, "registry_failure")
    assert response.json()["error"]["state_requires_verification"] is True
    assert deps.repository.read("clinic", "v002").status.value == "candidate"


def test_injection_and_isolation(runtime, tmp_path):
    deps = runtime[0]
    app = create_app(deps)
    other = HttpDependencies(FilesystemPromptRepository(tmp_path / "other"), deps.evaluation,
        deps.optimization, InMemoryOptimizationResultStore())
    app.dependency_overrides[get_dependencies] = lambda: other
    assert_error(TestClient(app).post("/optimizations", json=optimize_body()), 404, "not_found")
    app.dependency_overrides.clear()
    saved = TestClient(app).post("/optimizations", json=optimize_body()).json()
    assert_error(TestClient(create_app(other)).get(f"/optimizations/{saved['id']}"), 404, "not_found")
    snapshot = deps.results.get(saved["id"])
    snapshot["report"]["recommendation"] = "reject"
    assert TestClient(app).get(f"/optimizations/{saved['id']}").json() == saved


def test_handlers_run_off_event_loop(runtime, monkeypatch):
    import asyncio
    main_thread = get_ident()
    threads = []
    original = runtime[0].evaluation.execute
    def capture(*args, **kwargs):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        threads.append(get_ident())
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime[0].evaluation, "execute", capture)
    assert client(runtime).post("/optimizations", json=optimize_body()).status_code == 201
    assert threads and all(thread != main_thread for thread in threads)


def test_composition_and_import_no_io(tmp_path):
    command = "import prompt_optimizer.interfaces.http; import prompt_optimizer.http_runtime"
    result = subprocess.run([sys.executable, "-c", command], cwd=tmp_path, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.iterdir())
    from prompt_optimizer.http_runtime import build_offline_app, OfflineConfiguration
    app = build_offline_app(OfflineConfiguration(str(tmp_path / "repo"), "text", {}))
    assert_error(TestClient(app).post("/optimizations", json=optimize_body()), 404, "not_found")


def test_http_report_is_existing_contract(runtime):
    captured = []
    deps = runtime[0]
    class Capture:
        def write(self, report, destination):
            assert destination == "configured.json"
            captured.append(report)
    http = TestClient(create_app(HttpDependencies(deps.repository, deps.evaluation,
        deps.optimization, deps.results, Capture(), "configured.json")))
    response = http.post("/optimizations", json=optimize_body())
    assert response.status_code == 201
    assert response.json()["report"] == captured[0]
    assert OptimizationResponse.model_validate(response.json()).model_dump(mode="json") == response.json()


@pytest.mark.parametrize("action", ["approve", "reject", "promote"])
def test_lifecycle_rejects_unknown_body_fields(runtime, action):
    assert_error(client(runtime).post(f"/prompts/clinic/v001/{action}", json={"credentials": "SECRET"}),
                 422, "validation_error")


def test_candidate_evaluation_failure_does_not_save(runtime, monkeypatch):
    deps, llm, _, _ = runtime
    original = llm.generate
    def generate(prompt, **kwargs):
        if prompt.version == "v002":
            raise RuntimeError("SECRET provider failure")
        return original(prompt, **kwargs)
    monkeypatch.setattr(llm, "generate", generate)
    assert_error(client(runtime).post("/optimizations", json=optimize_body()), 500, "execution_failure")
    assert len(deps.repository.list("clinic")) == 1


def test_invalid_output_is_technical_failure(runtime, monkeypatch):
    monkeypatch.setattr(runtime[1], "generate", lambda *args, **kwargs: {"action": "escalate", "bad": float("nan")})
    assert_error(client(runtime).post("/optimizations", json=optimize_body()), 500, "execution_failure")
    assert len(runtime[0].repository.list("clinic")) == 1


def test_result_store_read_failure_safe(runtime, monkeypatch):
    def fail(identity):
        raise OSError("SECRET C:/private/path")
    monkeypatch.setattr(runtime[0].results, "get", fail)
    assert_error(client(runtime).get("/optimizations/id"), 500, "execution_failure")


def test_unexpected_injected_error_is_safe(runtime, monkeypatch):
    def fail(name):
        raise RuntimeError("SECRET C:/private/path")
    monkeypatch.setattr(runtime[0].repository, "list", fail)
    http = TestClient(create_app(runtime[0]), raise_server_exceptions=False)
    assert_error(http.get("/prompts/clinic/versions"), 500, "execution_failure")
