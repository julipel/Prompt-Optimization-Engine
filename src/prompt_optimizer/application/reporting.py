"""Pure, lossless report construction with an injected output port."""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Any
from prompt_optimizer.domain import EvaluationResult, OptimizationPipelineResult
from prompt_optimizer.ports import ReportWriter


def json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return json_value(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp must be aware")
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("Keys must be strings")
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    raise ValueError("Unsupported/nonfinite report value")


def evaluation_details(result: EvaluationResult) -> dict[str, Any]:
    return {**json_value(result), "failed_case_ids": json_value(result.failed_case_ids),
            "critical_failures": json_value(result.critical_failures)}


def build_report(result: EvaluationResult | OptimizationPipelineResult, *, started_at: datetime,
                 completed_at: datetime) -> dict[str, Any]:
    common = {"schema_version": "1.0", "timestamps": {
        "started_at": json_value(started_at), "completed_at": json_value(completed_at)}}
    if completed_at < started_at:
        raise ValueError("Completion precedes start")
    if isinstance(result, EvaluationResult):
        return {**common, "report_type": "evaluation", "evaluation": evaluation_details(result)}
    if not isinstance(result, OptimizationPipelineResult):
        raise TypeError("Unsupported report result")
    c = result.comparison
    return {**common, "report_type": "optimization", "task_id": result.task_id,
            "prompt_name": result.candidate.name, "baseline_version": result.baseline.prompt_version,
            "candidate_version": result.candidate.version, "dataset_id": result.baseline.dataset_id,
            "dataset_version": result.baseline.dataset_version, "optimization": json_value(result.optimization),
            "baseline": evaluation_details(result.baseline),
            "candidate_evaluation": evaluation_details(result.candidate_evaluation),
            "before": json_value(c.before), "after": json_value(c.after), "deltas": json_value(c.deltas),
            "gates": json_value(c.gates), "recommendation": result.recommendation.value}


def write_report(result: EvaluationResult | OptimizationPipelineResult, destination: str, *,
                 writer: ReportWriter, started_at: datetime, completed_at: datetime) -> None:
    writer.write(build_report(result, started_at=started_at, completed_at=completed_at), destination)


def terminal_summary(result: EvaluationResult | OptimizationPipelineResult) -> str:
    if isinstance(result, EvaluationResult):
        return (f"Evaluation {result.prompt_name}/{result.prompt_version}: scores={dict(result.scores)}\n"
                f"Failed cases: {list(result.failed_case_ids)}; critical failures: {dict(result.critical_failures)}")
    c = result.comparison
    return (f"Candidate {result.candidate.name}/{result.candidate.version}\n"
            f"Before: {dict(c.before)}\nAfter: {dict(c.after)}\nDelta: {dict(c.deltas)}\n"
            f"Failed cases: {list(result.candidate_evaluation.failed_case_ids)}; "
            f"critical failures: {dict(result.candidate_evaluation.critical_failures)}\n"
            f"Failed regression/comparison gates: {[g.name for g in c.gates if not g.passed]}; recommendation: {result.recommendation.value}")
