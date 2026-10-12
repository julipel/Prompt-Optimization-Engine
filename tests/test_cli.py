import json
from pathlib import Path
import subprocess
import sys

import pytest

from prompt_optimizer.cli import main


def test_module_help():
    result = subprocess.run(
        [sys.executable, "-m", "prompt_optimizer", "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "Prompt Optimization Engine" in result.stdout


def test_default_help(capsys):
    assert main([]) == 0
    assert "--version" in capsys.readouterr().out


def test_invalid_arguments():
    with pytest.raises(SystemExit) as error:
        main(["--unknown"])
    assert error.value.code == 2


def _jsonl(path, *rows):
    """Write rows as JSONL; a None row becomes an unparseable line."""
    path.write_text("".join("not json\n" if row is None else json.dumps(row, ensure_ascii=False) + "\n"
                            for row in rows), encoding="utf-8")
    return str(path)


def test_validate_dataset_accepts_demo_splits(capsys):
    root = Path(__file__).resolve().parents[1] / "datasets" / "clinic_demo"
    assert main(["validate-dataset", *(str(root / f"{s}.jsonl") for s in ("train", "validation", "test"))]) == 0
    out = capsys.readouterr().out
    assert "OK: 55 cases in 3 file(s)" in out
    assert 'intent: "booking"×14, "medical"×7, "price"×28, "unknown"×6' in out


def test_validate_dataset_reports_every_problem(tmp_path, capsys):
    train = _jsonl(tmp_path / "train.jsonl", {"id": "a", "input": "x", "expected": {"category": "billing"}},
                   {"id": "b", "input": "y", "expected": {"category": "Billing", "must_ask": "date"}})
    validation = _jsonl(tmp_path / "validation.jsonl", {"id": "a", "input": "z", "expected": {"category": "bug"}},
                        {"id": "c", "input": "x", "expected": {}})
    broken = _jsonl(tmp_path / "test.jsonl", {"id": "d", "input": "w", "expected": {"category": "bug"}}, None)
    assert main(["validate-dataset", train, validation, broken, str(tmp_path / "missing.jsonl")]) == 1
    captured = capsys.readouterr()
    for fragment in ("case 'b': expected.must_ask", "case 'a': id repeats a case in", "case 'c': expected is empty",
                     "case 'c': input repeats a case in", "test.jsonl:2: Invalid JSON", "missing.jsonl: Cannot read",
                     "Invalid: 6 problem(s)"):
        assert fragment in captured.err, fragment
    assert 'category: "Billing"×1, "billing"×1, "bug"×1' in captured.out


def test_validate_dataset_needs_files():
    with pytest.raises(SystemExit) as error:
        main(["validate-dataset"])
    assert error.value.code == 2
