"""Strict UTF-8 JSONL adapter with contextual validation errors."""

import json
from pathlib import Path
from typing import Any

from prompt_optimizer.application.datasets import DatasetLoadError
from prompt_optimizer.domain import Dataset, DomainValidationError, EvaluationCase


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


class JsonlDatasetLoader:
    """Read one JSON object per nonblank line; fail without partial results."""

    def load(self, source: str, *, dataset_id: str, version: str) -> Dataset:
        cases: list[EvaluationCase] = []
        seen: dict[str, int] = {}
        line_number: int | None = None
        try:
            with Path(source).open(encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line, object_pairs_hook=_object, parse_constant=_constant)
                    except ValueError as exc:
                        raise DatasetLoadError(source, f"Invalid JSON: {exc}", line=line_number) from exc
                    case_id = row.get("id") if isinstance(row, dict) else None
                    case_id = case_id if isinstance(case_id, str) else None
                    try:
                        if not isinstance(row, dict):
                            raise DomainValidationError("Case must be a JSON object")
                        required = {"id", "input", "expected"}
                        missing = required - row.keys()
                        if missing:
                            raise DomainValidationError(f"Missing required fields: {', '.join(sorted(missing))}")
                        unknown = row.keys() - (required | {"context", "tags"})
                        if unknown:
                            raise DomainValidationError(f"Unknown fields: {', '.join(sorted(unknown))}")
                        case = EvaluationCase(**row)
                        if case.id in seen:
                            raise DomainValidationError(f"Duplicate id; first occurrence at line {seen[case.id]}")
                    except DomainValidationError as exc:
                        raise DatasetLoadError(source, str(exc), line=line_number, case_id=case_id) from exc
                    seen[case.id] = line_number
                    cases.append(case)
        except (OSError, UnicodeError) as exc:
            raise DatasetLoadError(source, f"Cannot read UTF-8 dataset: {exc}", line=line_number) from exc
        try:
            return Dataset(id=dataset_id, version=version, cases=tuple(cases))
        except DomainValidationError as exc:
            raise DatasetLoadError(source, str(exc)) from exc
