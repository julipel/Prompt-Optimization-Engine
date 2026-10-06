import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from prompt_optimizer.adapters.jsonl_dataset import JsonlDatasetLoader
from prompt_optimizer.application import DatasetLoadError, load_dataset, load_train_validation
from prompt_optimizer.domain import Dataset, DomainValidationError, EvaluationCase


def write(tmp_path, content):
    path = tmp_path / "cases.jsonl"
    path.write_text(content, encoding="utf-8")
    return str(path)


def load(source):
    return load_dataset(source, dataset_id="clinic", version="1", loader=JsonlDatasetLoader())


def test_valid_file_and_optional_fields(tmp_path):
    rows = [
        {"id": "a", "input": "Цена?", "expected": {"price": 3500}},
        {"id": "b", "input": "Запись?", "expected": {},
         "context": {"service": "УЗИ"}, "tags": ["booking", "tool"]},
    ]
    source = write(tmp_path, "\n" + "\n\n".join(json.dumps(row, ensure_ascii=False) for row in rows))
    dataset = load(source)
    assert (dataset.id, dataset.version) == ("clinic", "1")
    assert [case.id for case in dataset.cases] == ["a", "b"]
    assert dataset.cases[0].input == "Цена?"
    assert dict(dataset.cases[0].context) == {}
    assert dataset.cases[0].tags == ()
    assert dataset.cases[1].tags == ("booking", "tool")
    assert dataset.cases[1].context["service"] == "УЗИ"


@pytest.mark.parametrize("content, reason, case_id", [
    ('{"id":', "Invalid JSON", None),
    ('{"id":"a","input":"x"}', "Missing required fields: expected", "a"),
    ('[]', "JSON object", None),
    ('null', "JSON object", None),
    ('{"id":"a","input":3,"expected":{}}', "input", "a"),
    ('{"id":"a","input":"x","expected":[]}', "expected", "a"),
    ('{"id":"a","input":"x","expected":{},"context":null}', "context", "a"),
    ('{"id":"a","input":"x","expected":{},"tags":"x"}', "tags", "a"),
    ('{"id":"a","input":"x","expected":{},"tags":[1]}', "tags", "a"),
    ('{"id":"a","input":"x","expected":{},"extra":1}', "Unknown fields", "a"),
    ('{"id":"a","id":"b","input":"x","expected":{}}', "Duplicate JSON key", None),
    ('{"id":"a","input":"x","expected":{"price":NaN}}', "Invalid JSON", None),
    ('{"id":"a","input":"x","expected":{"price":Infinity}}', "Invalid JSON", None),
    ('{"id":"a","input":"x","expected":{"price":1e999}}', "finite JSON", "a"),
])
def test_invalid_rows_report_physical_line(tmp_path, content, reason, case_id):
    source = write(tmp_path, "\n" + content)
    with pytest.raises(DatasetLoadError) as error:
        load(source)
    assert error.value.source == source
    assert error.value.line == 2
    assert error.value.case_id == case_id
    assert reason in str(error.value)
    assert error.value.__cause__ is not None


def test_duplicate_id(tmp_path):
    row = '{"id":"a","input":"x","expected":{}}'
    with pytest.raises(DatasetLoadError) as error:
        load(write(tmp_path, row + "\n\n" + row))
    assert error.value.line == 3
    assert error.value.case_id == "a"
    assert "first occurrence at line 1" in str(error.value)


@pytest.mark.parametrize("content", ["", "\n \t\n"])
def test_empty_dataset(tmp_path, content):
    with pytest.raises(DatasetLoadError, match="nonempty"):
        load(write(tmp_path, content))


def test_read_errors(tmp_path):
    with pytest.raises(DatasetLoadError, match="Cannot read"):
        load(str(tmp_path / "missing.jsonl"))
    path = tmp_path / "invalid.jsonl"
    path.write_bytes(b"\xff")
    with pytest.raises(DatasetLoadError, match="UTF-8"):
        load(str(path))


@pytest.mark.parametrize("changes", [
    {"id": " "}, {"version": ""}, {"cases": ()},
    {"cases": ["bad"]}, {"cases": "bad"},
])
def test_dataset_domain_validation(changes):
    values = dict(id="clinic", version="1", cases=[EvaluationCase("a", "x", {})])
    values.update(changes)
    with pytest.raises(DomainValidationError):
        Dataset(**values)


def test_dataset_immutable_and_unique():
    case = EvaluationCase("a", "x", {})
    cases = [case]
    dataset = Dataset("clinic", "1", cases)
    cases.clear()
    assert dataset.cases == (case,)
    with pytest.raises(FrozenInstanceError):
        dataset.version = "2"
    with pytest.raises(DomainValidationError, match="Duplicate"):
        Dataset("clinic", "1", (case, case))


def test_application_uses_injected_port():
    dataset = Dataset("clinic", "1", (EvaluationCase("a", "x", {}),))

    class FakeLoader:
        def load(self, source: str, *, dataset_id: str, version: str) -> Dataset:
            assert source in ("memory:train", "memory:validation")
            assert (dataset_id, version) == (dataset.id, dataset.version)
            return dataset

    assert load_train_validation(
        "memory:train", "memory:validation", dataset_id="clinic", version="1", loader=FakeLoader(),
    ) == (dataset, dataset)


def test_demo_train_validation():
    root = Path(__file__).resolve().parents[1] / "datasets" / "clinic"
    train, validation = load_train_validation(
        str(root / "train.jsonl"), str(root / "validation.jsonl"),
        dataset_id="clinic-demo", version="1", loader=JsonlDatasetLoader(),
    )
    assert len(train.cases) == len(validation.cases) == 4
    assert {case.id for case in train.cases}.isdisjoint(case.id for case in validation.cases)
    for split in (train, validation):
        assert {case.expected.get("intent") for case in split.cases} >= {"price", "booking", "unknown"}
        assert any(case.expected.get("action") == "escalate" for case in split.cases)


def test_validation_split_failure_is_propagated(tmp_path):
    source = write(tmp_path, '{"id":"a","input":"x","expected":{}}')
    with pytest.raises(DatasetLoadError) as error:
        load_train_validation(source, "missing.jsonl", dataset_id="clinic", version="1", loader=JsonlDatasetLoader())
    assert error.value.source == "missing.jsonl"
