"""Narrow capabilities available to an optimization agent; no lifecycle writes."""
from collections.abc import Mapping
from typing import Any, Protocol
from prompt_optimizer.domain import Dataset


ERROR_MESSAGES = {
    "input_validation": "Request does not satisfy the tool contract.",
    "unknown_dataset": "Dataset release is not configured.",
    "invalid_dataset": "Configured dataset splits are invalid.",
    "dataset_identity_mismatch": "Configured splits do not match the requested release.",
    "unknown_prompt": "Prompt was not found.",
    "unknown_version": "Source version was not found.",
    "production_not_found": "Prompt has no production version.",
    "report_not_found": "Report is unavailable in this app/process; do not rerun automatically.",
    "duplicate_identity": "Candidate identity already exists.",
    "lock_conflict": "Registry is busy; inspect stored state.",
    "corrupt_storage": "Storage requires operator inspection.",
    "registry_failure": "Registry operation failed; inspect stored state.",
    "execution_failure": "Optimization or report retrieval failed.",
    "result_storage_failure": "Candidate was saved but report storage failed.",
    "http_transport_failure": "HTTP transport failed; after POST the outcome is unknown. Verify registry before another optimization.",
    "timeout": "HTTP request timed out; after POST the outcome is unknown. Verify registry before another optimization.",
    "invalid_api_response": "API response violates the contract; after POST verify registry.",
}


class ToolError(RuntimeError):
    """Safe public exception; original technical exception is chained as cause."""
    def __init__(self, code: str, *, saved_candidate: Mapping[str, str] | None = None,
                 state_requires_verification: bool = False, outcome_unknown: bool = False) -> None:
        self.code = code
        self.saved_candidate = dict(saved_candidate) if saved_candidate is not None else None
        self.state_requires_verification = state_requires_verification
        self.outcome_unknown = outcome_unknown
        super().__init__(ERROR_MESSAGES[code])

    def payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": str(self),
            "saved_candidate": self.saved_candidate,
            "state_requires_verification": self.state_requires_verification,
            "outcome_unknown": self.outcome_unknown}}


class DatasetResolver(Protocol):
    def resolve(self, dataset_id: str, dataset_version: str) -> tuple[Dataset, Dataset]: ...


class OptimizationBackend(Protocol):
    def resolve_source(self, name: str, source_version: str | None, production: bool) -> str: ...
    def optimize(self, *, prompt_name: str, source_version: str, task_id: str,
                 candidate_version: str | None, train: Dataset, validation: Dataset) -> Mapping[str, Any]: ...
    def candidate_status(self, name: str, version: str) -> str: ...
    def get_report(self, report_id: str) -> Mapping[str, Any]: ...
