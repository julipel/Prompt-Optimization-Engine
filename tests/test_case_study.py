"""The recorded paid run in docs/case_study must replay exactly offline via the CLI."""
import json
from pathlib import Path

from prompt_optimizer.cli import main

ROOT = Path(__file__).resolve().parents[1]
CASE_STUDY = ROOT / "docs" / "case_study"
DATA = ROOT / "datasets" / "clinic_demo"


def replay(recorded: Path, workdir: Path) -> tuple[int, dict, dict[str, dict]]:
    """Rerun docs/CASE_STUDY.md's offline commands; return exit code and reports."""
    identity = ["--registry-root", str(workdir / "registry"), "--prompt-name", "clinic"]
    assert main(["bootstrap", *identity, "--prompt-version", "v001",
                 "--prompt-file", str(DATA / "baseline_prompt.txt")]) == 0
    report = workdir / "report.json"
    code = main(["optimize", *identity, "--prompt-version", "v001",
                 "--dataset-id", "clinic-demo", "--dataset-version", "2",
                 "--client", "fake", "--responses", str(recorded / "responses.json"),
                 "--train", str(DATA / "train.jsonl"), "--validation", str(DATA / "validation.jsonl"),
                 "--task-id", "clinic-case-study", "--optimizer", "fake",
                 "--candidate-file", str(recorded / "candidate_prompt.txt"), "--candidate-version", "v002",
                 "--report", str(report)])
    tests = {}
    for version in ("v001", "v002"):
        destination = workdir / f"test_{version}.json"
        assert main(["evaluate", *identity, "--prompt-version", version, "--dataset", str(DATA / "test.jsonl"),
                     "--dataset-id", "clinic-demo", "--dataset-version", "2", "--client", "fake",
                     "--responses", str(recorded / "responses.json"), "--report", str(destination)]) in (0, 3)
        tests[version] = json.loads(destination.read_text(encoding="utf-8"))
    return code, json.loads(report.read_text(encoding="utf-8")), tests


def test_recorded_case_study_replays_offline(tmp_path):
    recorded = json.loads((CASE_STUDY / "report.json").read_text(encoding="utf-8"))
    code, replayed, tests = replay(CASE_STUDY, tmp_path)
    assert code == {"approve": 0, "reject": 3, "review": 4}[recorded["recommendation"]]
    for key in ("before", "after", "deltas", "gates", "recommendation"):
        assert replayed[key] == recorded[key], key
    for split in ("baseline", "candidate_evaluation"):
        assert replayed[split]["cases"] == recorded[split]["cases"], split
    assert replayed["optimization"]["candidate"]["text"] == recorded["optimization"]["candidate"]["text"]
    for version, report in tests.items():
        expected = json.loads((CASE_STUDY / f"test_{version}.json").read_text(encoding="utf-8"))
        for key in ("prompt_version", "scores", "cases", "failed_case_ids", "critical_failures"):
            assert report["evaluation"][key] == expected["evaluation"][key], (version, key)
