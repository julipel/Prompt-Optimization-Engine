"""Strict HTTP DTOs; the domain remains the authoritative business contract."""
from typing import Annotated, Literal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator, field_validator
from prompt_optimizer.application.reporting import json_value
from prompt_optimizer.domain import Dataset, EvaluationCase

Identity = Annotated[str, Field(min_length=1, pattern=r"\S")]


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def finite_json(self) -> "BoundaryModel":
        json_value(self.model_dump())
        return self


class LifecycleRequest(BoundaryModel):
    """Lifecycle actions accept an absent body or an empty object."""


def reject_credentials(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.lower().replace("-", "_")
            if any(word in normalized for word in ("secret", "password", "credential", "api_key", "access_token", "authorization")):
                raise ValueError("Credential fields are forbidden")
            reject_credentials(item)
    elif isinstance(value, list):
        for item in value:
            reject_credentials(item)


class CaseRequest(BoundaryModel):
    id: Identity
    input: Identity
    expected: dict[str, JsonValue]
    context: dict[str, JsonValue] = Field(default_factory=dict)
    tags: list[Identity] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_case(self) -> "CaseRequest":
        reject_credentials(self.expected)
        reject_credentials(self.context)
        self.domain()
        return self

    def domain(self) -> EvaluationCase:
        return EvaluationCase(self.id, self.input, self.expected, self.context, tuple(self.tags))


class DatasetRequest(BoundaryModel):
    id: Identity
    version: Identity
    cases: list[CaseRequest] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dataset(self) -> "DatasetRequest":
        self.domain()
        return self

    def domain(self) -> Dataset:
        return Dataset(self.id, self.version, tuple(case.domain() for case in self.cases))


class EvaluationRequest(BoundaryModel):
    prompt_name: Identity
    prompt_version: Identity
    dataset: DatasetRequest


class OptimizationRequest(BoundaryModel):
    prompt_name: Identity
    prompt_version: Identity
    task_id: Identity
    candidate_version: Identity | None = None
    train: DatasetRequest
    validation: DatasetRequest

    @model_validator(mode="after")
    def paired_identity(self) -> "OptimizationRequest":
        if (self.train.id, self.train.version) != (self.validation.id, self.validation.version):
            raise ValueError("Train and validation must identify the same dataset release")
        if self.candidate_version == self.prompt_version:
            raise ValueError("Candidate version must differ from source")
        return self


class Timestamps(BoundaryModel):
    started_at: str
    completed_at: str

    @model_validator(mode="after")
    def aware_ordered(self) -> "Timestamps":
        start, end = datetime.fromisoformat(self.started_at), datetime.fromisoformat(self.completed_at)
        if start.utcoffset() is None or end.utcoffset() is None or end < start:
            raise ValueError("Timestamps must be aware and ordered")
        return self


class MetricResponse(BoundaryModel):
    name: str
    score: float
    passed: bool
    critical: bool
    details: str | None


class CaseResponse(BoundaryModel):
    case_id: str
    output: dict[str, JsonValue]
    metrics: list[MetricResponse]


class EvaluationDetails(BoundaryModel):
    prompt_name: str
    prompt_version: str
    dataset_id: str
    dataset_version: str
    cases: list[CaseResponse]
    scores: dict[str, float]
    failed_case_ids: list[str]
    critical_failures: dict[str, list[str]]


class EvaluationReport(BoundaryModel):
    schema_version: Literal["1.0"]
    report_type: Literal["evaluation"]
    timestamps: Timestamps
    evaluation: EvaluationDetails


class PromptResponse(BoundaryModel):
    name: str
    version: str
    text: str
    status: Literal["candidate", "approved", "rejected", "production", "archived"]
    parent_version: str | None
    optimizer: str | None
    dataset_id: str | None
    dataset_version: str | None
    scores: dict[str, float]
    created_at: str

    @field_validator("created_at")
    @classmethod
    def aware_timestamp(cls, value: str) -> str:
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("Timestamp must be aware")
        return value


class OptimizerResponse(BoundaryModel):
    task_id: str
    candidate: PromptResponse
    optimizer: str
    metadata: dict[str, JsonValue]


class GateResponse(BoundaryModel):
    name: str
    passed: bool
    reason: str


class OptimizationReport(BoundaryModel):
    schema_version: Literal["1.0"]
    report_type: Literal["optimization"]
    timestamps: Timestamps
    task_id: str
    prompt_name: str
    baseline_version: str
    candidate_version: str
    dataset_id: str
    dataset_version: str
    optimization: OptimizerResponse
    baseline: EvaluationDetails
    candidate_evaluation: EvaluationDetails
    before: dict[str, float]
    after: dict[str, float]
    deltas: dict[str, float | None]
    gates: list[GateResponse]
    recommendation: Literal["approve", "reject", "review"]


class OptimizationResponse(BoundaryModel):
    id: str
    report: OptimizationReport


class ProvenanceResponse(BoundaryModel):
    task_id: str | None
    metadata: dict[str, JsonValue]


class VersionResponse(BoundaryModel):
    version: str
    status: str
    parent_version: str | None
    provenance: ProvenanceResponse


class SavedIdentity(BoundaryModel):
    name: str
    version: str
    status: str


class ErrorDetails(BoundaryModel):
    code: str
    message: str
    saved_candidate: SavedIdentity | None = None
    state_requires_verification: bool = False


class ErrorResponse(BoundaryModel):
    error: ErrorDetails
