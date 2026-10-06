from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone, timedelta
import json
import os
import subprocess
import sys
from threading import Event
from unittest.mock import Mock

import pytest

from prompt_optimizer.adapters.filesystem_registry import FilesystemPromptRepository
from prompt_optimizer.adapters.fake_llm import FakeLLMClient
from prompt_optimizer.adapters.fake_optimizer import FakeOptimizer
from prompt_optimizer.application import (SaveCandidate, ApprovePrompt, RejectPrompt,
    PromotePrompt, EvaluatePrompt, OptimizePrompt)
from prompt_optimizer.domain import PromptVersion, PromptStatus, EvaluationCase, OptimizationTask
from prompt_optimizer.domain.evaluation import DeterministicEvaluator
from prompt_optimizer.domain.registry import (RegistryError, DuplicateVersionError,
    VersionNotFoundError, CorruptRegistryError, RegistryConflictError, InvalidTransitionError,
    RegistryProvenance)


@pytest.fixture
def repo(tmp_path):
    return FilesystemPromptRepository(tmp_path / "registry")


def prompt(version="v001", **kwargs):
    return PromptVersion("clinic", version, "  Юникод\r\nстрока\n \r\n", **kwargs)


def snapshot(repo):
    return repo.root / "prompts" / "clinic.json"


def test_roundtrip_and_duplicate(repo):
    original = prompt(optimizer="fake", dataset_id="ds", dataset_version="release",
        scores={"aggregate": .75}, created_at=datetime(2020, 1, 1, tzinfo=timezone(timedelta(hours=3))))
    provenance = RegistryProvenance("task", {"nested": ["ю", {"seed": 1}]})
    saved = repo.create(original, provenance=provenance)
    restarted = FilesystemPromptRepository(repo.root)
    assert restarted.read("clinic", "v001") == original == saved
    assert restarted.read("clinic", "v001").created_at.isoformat() == original.created_at.isoformat()
    assert restarted.provenance("clinic", "v001") == provenance
    before = snapshot(repo).read_bytes()
    with pytest.raises(DuplicateVersionError):
        repo.create(replace(original, text="replacement", scores={"aggregate": 1}))
    assert snapshot(repo).read_bytes() == before
    with pytest.raises(FrozenInstanceError):
        saved.text = "change"
    with pytest.raises(TypeError):
        saved.scores["aggregate"] = 1
    with pytest.raises(TypeError):
        provenance.metadata["x"] = 1


def test_order_gaps_and_next_version(repo):
    assert repo.next_version("clinic") == "v001"
    for v in ("v009", "v001", "v999"):
        repo.create(prompt(v))
    assert [p.version for p in repo.list("clinic")] == ["v001", "v009", "v999"]
    assert repo.next_version("clinic") == "v1000"
    repo.create(prompt("v1000"))
    assert repo.next_version("clinic") == "v1001"


@pytest.mark.parametrize("version", ["opaque", "v1", "v000", "v0001", "V001", "v１２３"])
def test_unsupported_generation_format(repo, version):
    repo.create(prompt(version))
    with pytest.raises(RegistryError, match="Unsupported version format") as error:
        repo.next_version("clinic")
    assert isinstance(error.value.__cause__, ValueError)


def test_parent_relations(repo):
    with pytest.raises(RegistryError, match="Parent"):
        repo.create(prompt("v002", parent_version="v001"))
    repo.create(prompt())
    repo.create(prompt("v002", parent_version="v001"))
    with pytest.raises(RegistryError, match="Parent"):
        repo.create(PromptVersion("other", "v002", "x", parent_version="v001"))
    with pytest.raises(ValueError):
        prompt(parent_version="v001")


def test_full_lifecycle_bootstrap_and_independence(repo):
    source = repo.bootstrap(prompt(status="production"))
    candidate = repo.create(prompt("v002", parent_version="v001"))
    approved = repo.approve("clinic", "v002")
    production = repo.promote("clinic", "v002")
    assert candidate.status is PromptStatus.CANDIDATE
    assert approved.status is PromptStatus.APPROVED
    assert production.status is PromptStatus.PRODUCTION
    assert source.status is PromptStatus.PRODUCTION
    assert repo.read("clinic", "v001").status is PromptStatus.ARCHIVED
    assert repo.production("clinic") == production
    assert len([p for p in repo.list("clinic") if p.status is PromptStatus.PRODUCTION]) == 1
    repo.bootstrap(PromptVersion("other", "v001", "x", status="production"))
    assert repo.production("other").version == "v001"
    assert [(e.operation, e.version) for e in repo.history("clinic")] == [
        ("bootstrap", "v001"), ("create", "v002"), ("approve", "v002"),
        ("archive", "v001"), ("promote", "v002")]
    assert FilesystemPromptRepository(repo.root).history("clinic") == repo.history("clinic")
    with pytest.raises(RegistryError):
        repo.bootstrap(prompt("v003", status="production"))


@pytest.mark.parametrize("status", ["approved", "rejected", "production", "archived"])
def test_create_cannot_import_lifecycle_status(repo, status):
    with pytest.raises(RegistryError):
        repo.create(prompt(status=status))
    assert repo.list("clinic") == ()


@pytest.mark.parametrize("operation", ["approve", "reject", "promote"])
def test_repeated_operations_fail(repo, operation):
    repo.create(prompt())
    if operation == "promote":
        repo.approve("clinic", "v001")
    getattr(repo, operation)("clinic", "v001")
    before = snapshot(repo).read_bytes()
    with pytest.raises(InvalidTransitionError) as error:
        getattr(repo, operation)("clinic", "v001")
    assert error.value.__cause__ is not None
    assert snapshot(repo).read_bytes() == before


@pytest.mark.parametrize("operation,status", [("promote", "candidate"), ("promote", "rejected"),
    ("promote", "archived"), ("approve", "approved"), ("reject", "approved"),
    ("reject", "production"), ("approve", "production")])
def test_invalid_transitions(repo, operation, status):
    repo.create(prompt())
    if status in ("approved", "archived", "production"):
        repo.approve("clinic", "v001")
    if status in ("production", "archived"):
        repo.promote("clinic", "v001")
    if status == "rejected":
        repo.reject("clinic", "v001")
    if status == "archived":
        repo.create(prompt("v002"))
        repo.approve("clinic", "v002")
        repo.promote("clinic", "v002")
    with pytest.raises(InvalidTransitionError):
        getattr(repo, operation)("clinic", "v001")


def test_missing_is_distinct(repo):
    with pytest.raises(VersionNotFoundError) as error:
        repo.read("clinic", "v001")
    assert isinstance(error.value.__cause__, KeyError)
    assert repo.production("clinic") is None
    assert repo.history("clinic") == ()


@pytest.mark.parametrize("damage", ["json", "incomplete", "parent", "status", "history",
    "timestamp", "identity", "duplicate_key", "archive", "provenance", "schema", "extra"])
def test_corrupt_records_are_never_skipped(repo, damage):
    repo.create(prompt())
    path = snapshot(repo)
    state = json.loads(path.read_text(encoding="utf-8"))
    p = state["records"]["v001"]["prompt"]
    if damage == "json":
        path.write_text("{", encoding="utf-8")
    elif damage == "duplicate_key":
        path.write_text('{"schema":1,"schema":1}', encoding="utf-8")
    else:
        if damage == "incomplete": del p["text"]
        if damage == "parent": p["parent_version"] = "v002"
        if damage == "status": p["status"] = "production"
        if damage == "history": state["history"] = []
        if damage == "timestamp": p["created_at"] = "2020-01-01"
        if damage == "identity": p["name"] = "other"
        if damage == "archive": state["history"][0]["operation"] = "archive"
        if damage == "provenance": state["records"]["v001"]["provenance"]["metadata"] = {"api_key": "x"}
        if damage == "schema": state["schema"] = True
        if damage == "extra": state["extra"] = 1
        path.write_text(json.dumps(state), encoding="utf-8")
    for call in (lambda: repo.list("clinic"), lambda: repo.read("clinic", "v001"),
                 lambda: repo.next_version("clinic"), lambda: repo.production("clinic")):
        with pytest.raises(CorruptRegistryError) as error:
            call()
        assert error.value.__cause__ is not None


@pytest.mark.parametrize("failure", ["replace", "fsync", "serialize", "temporary"])
def test_failed_write_keeps_prior_snapshot(repo, monkeypatch, failure):
    repo.bootstrap(prompt(status="production"))
    repo.create(prompt("v002"))
    repo.approve("clinic", "v002")
    before = snapshot(repo).read_bytes()
    targets = {"replace": "os.replace", "fsync": "os.fsync", "serialize": "json.dumps",
               "temporary": "tempfile.NamedTemporaryFile"}
    with monkeypatch.context() as patch:
        patch.setattr("prompt_optimizer.adapters.filesystem_registry." + targets[failure],
                      Mock(side_effect=OSError("injected failure")))
        with pytest.raises(RegistryError) as error:
            repo.promote("clinic", "v002")
        assert isinstance(error.value.__cause__, OSError)
        assert error.value.operation == "promote"
    assert snapshot(repo).read_bytes() == before
    assert repo.production("clinic").version == "v001"
    assert repo.read("clinic", "v002").status is PromptStatus.APPROVED
    assert not list(snapshot(repo).parent.glob(".registry-*.tmp"))


def test_read_io_failure(repo, monkeypatch):
    repo.create(prompt())
    monkeypatch.setattr(type(snapshot(repo)), "read_text", Mock(side_effect=PermissionError("denied")))
    with pytest.raises(RegistryError) as error:
        repo.read("clinic", "v001")
    assert not isinstance(error.value, VersionNotFoundError)
    assert isinstance(error.value.__cause__, PermissionError)


def test_conflicting_instances_fail_fast(repo, monkeypatch):
    entered, release = Event(), Event()
    original = repo._write
    def blocked(path, state):
        entered.set()
        assert release.wait(5)
        original(path, state)
    monkeypatch.setattr(repo, "_write", blocked)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(repo.create, prompt())
        assert entered.wait(5)
        try:
            other = FilesystemPromptRepository(repo.root)
            with pytest.raises(RegistryConflictError):
                other.create(prompt())
            with pytest.raises(RegistryConflictError):
                other.list("clinic")
            other.create(PromptVersion("other", "v001", "x"))
        finally:
            release.set()
        assert future.result().version == "v001"
    with pytest.raises(DuplicateVersionError):
        other.create(prompt())


@pytest.mark.parametrize("identifier", ["../escape", "..", ".", "C:\\escape", "/absolute", "a/b", "a\\b",
    "CON", "CONIN$", "CONOUT$", "CLOCK$", "nul.txt", "COM1", "LPT9.md", "com¹", "trailing.", "trailing ", "a:b", "a?b", "a\x00b"])
@pytest.mark.parametrize("field", ["name", "version"])
def test_unsafe_identifiers(repo, identifier, field):
    with pytest.raises(RegistryError) as error:
        repo.create(replace(prompt(), **{field: identifier}))
    assert error.value.__cause__ is not None
    assert repo.list("clinic") == ()


def test_case_collisions(repo):
    repo.create(prompt())
    with pytest.raises(CorruptRegistryError, match="collision"):
        repo.create(replace(prompt("v002"), name="CLINIC"))
    with pytest.raises(DuplicateVersionError):
        repo.create(prompt("V001"))
    assert len(repo.list("clinic")) == 1


def test_symlink_rejected(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        repo.root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit symlink creation")
    with pytest.raises(RegistryError, match="Symlinks"):
        repo.create(prompt())
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("key", ["api_key", "Authorization", "nested_password", "access-token"])
def test_provenance_rejects_secret_keys(key):
    with pytest.raises(ValueError):
        RegistryProvenance("t", {"nested": [{key: "sensitive"}]})


@pytest.mark.parametrize("answer,recommendation", [("escalate", "approve"), ("answer", "review"), ("unsafe", "reject")])
def test_offline_pipeline_explicit_lifecycle(repo, answer, recommendation):
    repo.bootstrap(prompt(status="production"))
    source = repo.production("clinic")
    case = EvaluationCase("val", "Неизвестная услуга?", {"action": "escalate"})
    task = OptimizationTask("t", source, (replace(case, id="train"),), (case,), "ds", "1")
    candidate_version = repo.next_version("clinic")
    outputs = {"action": answer}
    if answer == "unsafe": outputs["unsafe_action"] = True
    client = FakeLLMClient({}, version_responses={
        ("v001", "val"): {"action": "answer"}, (candidate_version, "val"): outputs})
    result = OptimizePrompt(FakeOptimizer("Лучший текст"),
        EvaluatePrompt(client, [DeterministicEvaluator()])).execute(task, candidate_version=candidate_version)
    assert result.recommendation.value == recommendation
    assert len(repo.history("clinic")) == 1
    fake_repository = Mock()
    fake_repository.create.side_effect = lambda candidate, **kwargs: candidate
    fake_saved = SaveCandidate(fake_repository).execute(result)
    assert fake_saved.scores == result.candidate_evaluation.scores
    assert fake_repository.method_calls == [
        ("create", (fake_saved,), {"provenance": RegistryProvenance("t", {"offline": True})})]
    candidate = SaveCandidate(repo).execute(result)
    assert candidate.scores == result.candidate_evaluation.scores
    assert candidate.status is PromptStatus.CANDIDATE
    assert repo.production("clinic") == source
    assert repo.provenance("clinic", candidate_version) == RegistryProvenance("t", {"offline": True})
    assert result.candidate.scores == {}
    if recommendation == "approve":
        ApprovePrompt(repo).execute("clinic", candidate_version)
        PromotePrompt(repo).execute("clinic", candidate_version)
        assert repo.production("clinic").version == candidate_version
    else:
        assert len(repo.history("clinic")) == 2
        RejectPrompt(repo).execute("clinic", candidate_version)
        assert repo.production("clinic") == source


@pytest.mark.parametrize("usecase,method", [(ApprovePrompt, "approve"), (RejectPrompt, "reject"), (PromotePrompt, "promote")])
def test_application_injected_port(usecase, method):
    repository = Mock()
    value = usecase(repository).execute("p", "opaque")
    getattr(repository, method).assert_called_once_with("p", "opaque")
    assert value is getattr(repository, method).return_value


def test_failed_creation_does_not_occupy_version(repo, monkeypatch):
    repo.create(prompt())
    before = snapshot(repo).read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr("prompt_optimizer.adapters.filesystem_registry.os.replace",
                      Mock(side_effect=OSError("failure")))
        with pytest.raises(RegistryError):
            repo.create(prompt("v002"))
    assert snapshot(repo).read_bytes() == before
    repo.create(prompt("v002"))
    assert len(repo.list("clinic")) == 2


def test_conflicting_promotions(repo, monkeypatch):
    repo.bootstrap(prompt(status="production"))
    for v in ("v002", "v003"):
        repo.create(prompt(v))
        repo.approve("clinic", v)
    entered, release = Event(), Event()
    original = repo._write
    def blocked(path, state):
        entered.set()
        assert release.wait(5)
        original(path, state)
    monkeypatch.setattr(repo, "_write", blocked)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(repo.promote, "clinic", "v002")
        assert entered.wait(5)
        try:
            with pytest.raises(RegistryConflictError):
                FilesystemPromptRepository(repo.root).promote("clinic", "v003")
        finally:
            release.set()
        assert future.result().status is PromptStatus.PRODUCTION
    assert repo.production("clinic").version == "v002"
    assert repo.read("clinic", "v003").status is PromptStatus.APPROVED


def test_interprocess_lock(repo):
    repo.create(prompt())
    # Parent holds the same OS lock used by every adapter operation.
    code = '''
import sys
from prompt_optimizer.adapters.filesystem_registry import FilesystemPromptRepository
from prompt_optimizer.domain.registry import RegistryConflictError
try:
    FilesystemPromptRepository(sys.argv[1]).approve("clinic", "v001")
except RegistryConflictError:
    sys.exit(0)
sys.exit(1)
'''
    with repo._access("test", "clinic"):
        result = subprocess.run([sys.executable, "-c", code, str(repo.root)],
                                capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert repo.read("clinic", "v001").status is PromptStatus.CANDIDATE


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_windows_junction_cannot_escape_root(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    repo.root.mkdir()
    junction = repo.root / "prompts"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    with pytest.raises(RegistryError, match="junction|Reparse"):
        repo.create(prompt())
    assert list(outside.iterdir()) == []


def test_corrupt_multiple_production_and_parent_cycle(repo):
    repo.bootstrap(prompt(status="production"))
    repo.create(prompt("v002", parent_version="v001"))
    repo.approve("clinic", "v002")
    path = snapshot(repo)
    state = json.loads(path.read_text(encoding="utf-8"))
    state["records"]["v002"]["prompt"]["status"] = "production"
    state["history"].append(dict(operation="promote", version="v002", previous="approved",
                                status="production", at=state["history"][-1]["at"]))
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(CorruptRegistryError, match="Multiple production"):
        repo.list("clinic")
    state["records"]["v001"]["prompt"]["parent_version"] = "v002"
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(CorruptRegistryError, match="Parent"):
        repo.list("clinic")
