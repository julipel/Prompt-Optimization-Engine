"""Strict SDK-independent tool boundary, reusing the HTTP report 1.0 schema."""
from typing import Annotated, Literal
from pydantic import ConfigDict, Field, model_validator
from .http_schemas import (BoundaryModel, Identity, GateResponse, OptimizationResponse,
                           SavedIdentity)

Status = Literal["candidate", "approved", "rejected", "production", "archived"]
Score = Annotated[float, Field(ge=0, le=1)]
Delta = Annotated[float, Field(ge=-1, le=1)]
ErrorCode = Literal["input_validation", "unknown_dataset", "invalid_dataset",
    "dataset_identity_mismatch", "unknown_prompt", "unknown_version", "production_not_found",
    "report_not_found", "duplicate_identity", "lock_conflict", "corrupt_storage",
    "registry_failure", "execution_failure", "result_storage_failure",
    "http_transport_failure", "timeout", "invalid_api_response"]


class OptimizePromptRequest(BoundaryModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False,
        json_schema_extra={"oneOf": [
            {"required": ["source_version"], "properties": {"source_version": {"type": "string", "minLength": 1}, "production": {"const": False}}},
            {"required": ["production"], "properties": {"production": {"const": True}, "source_version": {"type": "null"}}},
        ]})
    prompt_name: Identity
    source_version: Identity | None = None
    production: bool = False
    dataset_id: Identity
    dataset_version: Identity
    candidate_version: Identity | None = None

    @model_validator(mode="after")
    def source_selection(self) -> "OptimizePromptRequest":
        if (self.source_version is not None) == self.production:
            raise ValueError("Choose exactly one source_version or production=true")
        if self.candidate_version is not None and self.candidate_version == self.source_version:
            raise ValueError("Candidate must differ from source")
        return self


class ReportRequest(BoundaryModel):
    report_id: Identity


class CandidateIdentity(BoundaryModel):
    name: Identity
    version: Identity
    status: Status


class Failures(BoundaryModel):
    failed_case_ids: list[Identity]
    critical_failures: dict[str, list[Identity]]


class ReportReference(BoundaryModel):
    id: Identity
    schema_version: Literal["1.0"] = "1.0"
    retrieval_tool: Literal["get_optimization_report"] = "get_optimization_report"
    method: Literal["GET"] = "GET"
    api_path: Identity
    lifetime: Literal["http_app_process_memory"] = "http_app_process_memory"


class OptimizePromptResponse(BoundaryModel):
    candidate: CandidateIdentity
    source_version: Identity
    dataset_id: Identity
    dataset_version: Identity
    before: dict[Identity, Score]
    after: dict[Identity, Score]
    deltas: dict[Identity, Delta | None]
    gates: list[GateResponse] = Field(min_length=1)
    violations: list[GateResponse]
    baseline_failures: Failures
    candidate_failures: Failures
    recommendation: Literal["approve", "reject", "review"]
    report: ReportReference

    @model_validator(mode="after")
    def violations_match(self) -> "OptimizePromptResponse":
        if self.violations != [gate for gate in self.gates if not gate.passed]:
            raise ValueError("Violations must be the failed comparison gates")
        return self


class ToolErrorDetails(BoundaryModel):
    code: ErrorCode
    message: str
    saved_candidate: SavedIdentity | None
    state_requires_verification: bool
    outcome_unknown: bool


class ToolErrorResponse(BoundaryModel):
    error: ToolErrorDetails


def validate_report(value: object) -> OptimizationResponse:
    """Validate identities and detail consistency without re-running comparison."""
    result = OptimizationResponse.model_validate(value)
    ReportRequest(report_id=result.id)
    r = result.report
    c = r.optimization.candidate
    for identity in (r.task_id, r.prompt_name, r.baseline_version, r.candidate_version,
                     r.dataset_id, r.dataset_version, c.optimizer):
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("Empty report identity")
    if r.baseline_version == r.candidate_version:
        raise ValueError("Candidate must differ from baseline")
    if (c.name, c.version, c.parent_version, c.dataset_id, c.dataset_version, c.status) != (
        r.prompt_name, r.candidate_version, r.baseline_version, r.dataset_id, r.dataset_version, "candidate"):
        raise ValueError("Candidate identity mismatch")
    if r.optimization.task_id != r.task_id or r.optimization.optimizer != c.optimizer:
        raise ValueError("Provenance mismatch")
    for evaluation, version, scores in ((r.baseline, r.baseline_version, r.before),
                                        (r.candidate_evaluation, r.candidate_version, r.after)):
        if (evaluation.prompt_name, evaluation.prompt_version, evaluation.dataset_id, evaluation.dataset_version) != (
            r.prompt_name, version, r.dataset_id, r.dataset_version):
            raise ValueError("Evaluation identity mismatch")
        if evaluation.scores != scores or not evaluation.cases:
            raise ValueError("Scores/splits mismatch")
        ids = [case.case_id for case in evaluation.cases]
        if any(not case_id.strip() for case_id in ids) or len(set(ids)) != len(ids):
            raise ValueError("Duplicate case ids")
        failed = [case.case_id for case in evaluation.cases if any(not m.passed for m in case.metrics)]
        critical = {case.case_id: [m.name for m in case.metrics if m.critical and not m.passed]
                    for case in evaluation.cases if any(m.critical and not m.passed for m in case.metrics)}
        if evaluation.failed_case_ids != failed or evaluation.critical_failures != critical:
            raise ValueError("Failure detail mismatch")
        for case in evaluation.cases:
            if not case.metrics or len({m.name for m in case.metrics}) != len(case.metrics):
                raise ValueError("Invalid metrics")
            if any(not 0 <= m.score <= 1 for m in case.metrics):
                raise ValueError("Invalid metric score")
            if any(not m.name.strip() for m in case.metrics):
                raise ValueError("Empty metric name")
        if any(not 0 <= score <= 1 for score in scores.values()):
            raise ValueError("Invalid aggregate score")
    if [case.case_id for case in r.baseline.cases] != [case.case_id for case in r.candidate_evaluation.cases]:
        raise ValueError("Validation cases mismatch")
    if set(r.deltas) != set(r.before) | set(r.after):
        raise ValueError("Delta keys mismatch")
    if any(delta is not None and not -1 <= delta <= 1 for delta in r.deltas.values()):
        raise ValueError("Invalid delta range")
    if not r.gates or len({gate.name for gate in r.gates}) != len(r.gates) or any(not gate.name.strip() for gate in r.gates):
        raise ValueError("Invalid comparison gates")
    return result
