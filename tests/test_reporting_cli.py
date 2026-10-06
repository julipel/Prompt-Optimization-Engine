import json
from datetime import datetime, timezone
from types import MappingProxyType
import pytest
from prompt_optimizer.cli import main
from prompt_optimizer.adapters.filesystem_registry import FilesystemPromptRepository
from prompt_optimizer.adapters.json_report import JsonReportWriter, ReportWriteError
from prompt_optimizer.application.reporting import json_value


@pytest.fixture
def workflow(tmp_path):
    def write(name, content):
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return str(path)
    prompt = write("prompt.txt", "Передавай оператору")
    candidate = write("candidate.txt", "Не выдумывай цены")
    dataset = write("cases.jsonl", json.dumps({"id": "case", "input": "Цена?",
        "expected": {"action": "escalate", "must_not_invent": True}}, ensure_ascii=False))
    responses = write("responses.json", json.dumps({
        "v001": {"case": {"action": "answer"}},
        "v002": {"case": {"action": "escalate", "note": "Проверено"}},
        "v003": {"case": {"action": "escalate"}}}, ensure_ascii=False))
    base = ["--registry-root", str(tmp_path / "registry"), "--prompt-name", "clinic"]
    identity = [*base, "--prompt-version", "v001"]
    shared = ["--dataset-id", "demo", "--dataset-version", "1", "--client", "fake",
              "--responses", responses]
    report = str(tmp_path / "report.json")
    assert main(["bootstrap", *identity, "--prompt-file", prompt]) == 0
    return tmp_path, base, identity, shared, dataset, candidate, report


def optimize_args(w, version=None):
    _, _, identity, shared, dataset, candidate, report = w
    args = ["optimize", *identity, *shared, "--train", dataset, "--validation", dataset,
            "--task-id", "task-07", "--optimizer", "fake", "--candidate-file", candidate,
            "--report", report]
    return args + (["--candidate-version", version] if version else [])


def test_full_lifecycle(workflow, capsys):
    path, base, identity, shared, dataset, _, report = workflow
    assert main(["evaluate", *identity, *shared, "--dataset", dataset, "--report", report]) == 3
    evaluation = json.loads(open(report, encoding="utf-8").read())
    assert evaluation["report_type"] == "evaluation"
    assert evaluation["evaluation"]["failed_case_ids"] == ["case"]
    assert main(optimize_args(workflow)) == 0
    data = json.loads(open(report, encoding="utf-8").read())
    assert data["schema_version"] == "1.0"
    assert (data["prompt_name"], data["baseline_version"], data["candidate_version"],
            data["dataset_id"], data["dataset_version"]) == ("clinic", "v001", "v002", "demo", "1")
    assert data["recommendation"] == "approve"
    assert data["before"]["aggregate"] < data["after"]["aggregate"]
    assert data["deltas"]["aggregate"] > 0
    assert data["gates"] and data["task_id"] == "task-07"
    assert data["candidate_evaluation"]["cases"][0]["output"]["note"] == "Проверено"
    assert data["candidate_evaluation"]["cases"][0]["metrics"][0]["details"]
    assert datetime.fromisoformat(data["timestamps"]["completed_at"]).tzinfo
    assert datetime.fromisoformat(data["timestamps"]["started_at"]) <= datetime.fromisoformat(
        data["timestamps"]["completed_at"])
    repo = FilesystemPromptRepository(path / "registry")
    assert repo.read("clinic", "v002").status.value == "candidate"
    assert repo.production("clinic").version == "v001"
    assert dict(repo.read("clinic", "v002").scores) == data["after"]
    assert repo.provenance("clinic", "v002").task_id == "task-07"
    assert repo.provenance("clinic", "v002").metadata["offline"] is True
    assert main(["versions", *base]) == 0
    assert 'task-07' in capsys.readouterr().out
    for command in ("approve", "promote"):
        assert main([command, *base, "--prompt-version", "v002"]) == 0
    assert repo.production("clinic").version == "v002"
    assert repo.read("clinic", "v001").status.value == "archived"
    assert main(optimize_args(workflow, "v003")) == 0
    assert main(["reject", *base, "--prompt-version", "v003"]) == 0
    assert repo.read("clinic", "v003").status.value == "rejected"
    assert main(["reject", *base, "--prompt-version", "v003"]) == 1


@pytest.mark.parametrize("response,code,recommendation", [
    ({"action": "answer"}, 4, "review"),
    ({"action": "escalate", "price": 999}, 3, "reject")])
def test_recommendations(workflow, response, code, recommendation):
    path, _, _, shared, _, _, report = workflow
    responses = path / "responses.json"
    data = json.loads(responses.read_text(encoding="utf-8"))
    data["v002"]["case"] = response
    responses.write_text(json.dumps(data), encoding="utf-8")
    assert main(optimize_args(workflow)) == code
    result = json.loads(open(report, encoding="utf-8").read())
    assert result["recommendation"] == recommendation
    if code == 3:
        assert result["candidate_evaluation"]["critical_failures"]["case"]
    assert FilesystemPromptRepository(path / "registry").read("clinic", "v002").status.value == "candidate"


def test_report_failure_saved_candidate(workflow, capsys):
    class Broken:
        def write(self, report, destination):
            raise OSError("SECRET credential")
    assert main(optimize_args(workflow), report_writer=Broken()) == 1
    error = capsys.readouterr().err
    assert "clinic/v002" in error and "remains candidate" in error
    assert "SECRET" not in error


def test_injected_writer(workflow):
    class Capture:
        def write(self, report, destination):
            self.report = report
    writer = Capture()
    assert main(optimize_args(workflow), report_writer=writer) == 0
    assert writer.report["optimization"]["metadata"] == {"offline": True}


@pytest.mark.parametrize("command", ["bootstrap", "evaluate", "optimize", "versions", "approve", "reject", "promote"])
def test_command_arguments(command):
    with pytest.raises(SystemExit) as error:
        main([command])
    assert error.value.code == 2
    with pytest.raises(SystemExit) as error:
        main([command, "--help"])
    assert error.value.code == 0


def test_storage_failures(workflow):
    path, base, identity, *_ = workflow
    assert main(["approve", *identity]) == 1
    assert main(["approve", *base, "--prompt-version", "missing"]) == 1
    lock = path / "registry" / "prompts" / "clinic.lock"
    lock.mkdir()
    assert main(["versions", *base]) == 1
    lock.rmdir()
    record = path / "registry" / "prompts" / "clinic.json"
    record.write_text("broken", encoding="utf-8")
    assert main(["versions", *base]) == 1


def test_duplicate_bootstrap_and_candidate(workflow):
    path, _, identity, *_ = workflow
    assert main(["bootstrap", *identity, "--prompt-file", str(path / "prompt.txt")]) == 1
    assert main(optimize_args(workflow)) == 0
    assert main(optimize_args(workflow, "v002")) == 1


def test_writer_atomic_unicode_and_failure(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    writer = JsonReportWriter()
    writer.write({"unicode": "Привет", "tuple": (1, 2)}, str(path))
    old = path.read_bytes()
    assert "Привет" in path.read_text(encoding="utf-8")
    def fail(*args):
        raise OSError("disk")
    monkeypatch.setattr("prompt_optimizer.adapters.json_report.os.replace", fail)
    with pytest.raises(ReportWriteError) as error:
        writer.write({"new": True}, str(path))
    assert isinstance(error.value.__cause__, OSError)
    assert path.read_bytes() == old
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object(), datetime.now()])
def test_invalid_report_values(value, tmp_path):
    with pytest.raises(ReportWriteError):
        JsonReportWriter().write({"value": value}, str(tmp_path / "report.json"))


def test_immutable_serialization():
    assert json_value(MappingProxyType({"nested": (MappingProxyType({"text": "Да"}),)})) == {
        "nested": [{"text": "Да"}]}




def test_version(capsys):
    from prompt_optimizer import __version__
    with pytest.raises(SystemExit) as error:
        main(["--version"])
    assert error.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_successful_evaluation_and_real_report_failure(workflow, capsys):
    path, base, identity, shared, dataset, _, report = workflow
    assert main(optimize_args(workflow)) == 0
    assert main(["evaluate", *base, "--prompt-version", "v002", *shared,
                 "--dataset", dataset, "--report", report]) == 0
    args = optimize_args(workflow, "v003")
    args[args.index("--report") + 1] = str(path / "missing" / "report.json")
    assert main(args) == 1
    assert "clinic/v003" in capsys.readouterr().err
    assert FilesystemPromptRepository(path / "registry").read("clinic", "v003").status.value == "candidate"


def test_missing_response_is_safe(workflow, capsys):
    path, *_ = workflow
    (path / "responses.json").write_text('{}', encoding="utf-8")
    assert main(optimize_args(workflow)) == 1
    assert "KeyError" not in capsys.readouterr().err
    assert len(FilesystemPromptRepository(path / "registry").list("clinic")) == 1


def test_cli_invalid_choice():
    with pytest.raises(SystemExit) as error:
        main(["evaluate", "--client", "unknown"])
    assert error.value.code == 2


def test_report_enum_datetime(tmp_path):
    from prompt_optimizer.domain import PromptStatus
    now = datetime.now(timezone.utc)
    path = tmp_path / 'report.json'
    JsonReportWriter().write({'status': PromptStatus.CANDIDATE, 'time': now}, str(path))
    assert json.loads(path.read_text(encoding='utf-8')) == {
        'status': 'candidate', 'time': now.isoformat()}
