"""HTTP adapter for the narrow tool port. Exactly one request, never retries."""
from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import quote, urlsplit
import httpx
from pydantic import TypeAdapter
from prompt_optimizer.application.reporting import json_value
from prompt_optimizer.domain import Dataset
from prompt_optimizer.ports.agent_tool import ToolError
from prompt_optimizer.interfaces.http_schemas import (ErrorResponse, PromptResponse,
    VersionResponse, OptimizationRequest)
from prompt_optimizer.interfaces.agent_schemas import validate_report


def segment(value: str) -> str:
    return quote(value, safe="").replace(".", "%2E")


@dataclass(frozen=True)
class HttpBackendConfiguration:
    base_url: str
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        from math import isfinite
        url = urlsplit(self.base_url)
        if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise ValueError("Configure an HTTP base URL without credentials/query/fragment")
        if not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Timeout must be positive and finite")


def strict_json(raw: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("Nonfinite JSON constant")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


class HttpOptimizationBackend:
    def __init__(self, config: HttpBackendConfiguration, *, transport: httpx.BaseTransport | None = None,
                 client: Any | None = None) -> None:
        """Injected TestClient/client owns its lifetime; otherwise close this adapter."""
        if client is not None and transport is not None:
            raise ValueError("Choose either client or transport")
        self._config = config
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(transport=transport, follow_redirects=False)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request(self, method: str, path: str, *, missing: str, body: dict | None = None) -> Any:
        posted = method == "POST"
        try:
            response = self._client.request(method, self._config.base_url.rstrip("/") + path,
                json=body, timeout=self._config.timeout_seconds, follow_redirects=False)
        except httpx.TimeoutException as exc:
            raise ToolError("timeout", state_requires_verification=posted, outcome_unknown=posted) from exc
        except httpx.TransportError as exc:
            raise ToolError("http_transport_failure", state_requires_verification=posted, outcome_unknown=posted) from exc
        except Exception as exc:
            raise ToolError("http_transport_failure", state_requires_verification=posted, outcome_unknown=posted) from exc
        try:
            value = strict_json(response.text)
            if response.status_code != (201 if posted else 200):
                error = ErrorResponse.model_validate(value).error
                codes = {"not_found": missing, "validation_error": "input_validation",
                    **{code: code for code in ("duplicate_identity", "lock_conflict", "corrupt_storage",
                       "registry_failure", "execution_failure", "result_storage_failure")}}
                code = codes.get(error.code)
                expected_status = {"not_found": 404, "validation_error": 422,
                    "duplicate_identity": 409, "lock_conflict": 409,
                    "corrupt_storage": 500, "registry_failure": 500,
                    "execution_failure": 500, "result_storage_failure": 500}
                if code is None or response.status_code != expected_status[error.code]:
                    raise ValueError("Unknown HTTP error contract")
                saved = error.saved_candidate
                if saved is not None and (not saved.name.strip() or not saved.version.strip() or saved.status != "candidate"):
                    raise ValueError("Invalid saved identity")
                raise ToolError(code, saved_candidate=saved.model_dump() if saved else None,
                    state_requires_verification=error.state_requires_verification or saved is not None) from httpx.HTTPStatusError(
                        "Optimization API returned an error.", request=response.request, response=response)
            return value
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError("invalid_api_response", state_requires_verification=posted, outcome_unknown=posted) from exc

    def _versions(self, name: str) -> list[VersionResponse]:
        value = self._request("GET", f"/prompts/{segment(name)}/versions", missing="unknown_prompt")
        try:
            versions = TypeAdapter(list[VersionResponse]).validate_python(value, strict=True)
            if not versions or len({v.version for v in versions}) != len(versions):
                raise ValueError("Invalid version identities")
            if any(not v.version.strip() or v.status not in ("candidate", "approved", "rejected", "production", "archived") for v in versions):
                raise ValueError("Invalid version/status")
            return versions
        except Exception as exc:
            raise ToolError("invalid_api_response") from exc

    def resolve_source(self, name: str, source_version: str | None, production: bool) -> str:
        versions = self._versions(name)
        if production:
            value = self._request("GET", f"/prompts/{segment(name)}/production", missing="production_not_found")
            try:
                prompt = PromptResponse.model_validate(value)
                if prompt.name != name or prompt.status != "production" or not prompt.version.strip():
                    raise ValueError("Invalid production identity")
                return prompt.version
            except Exception as exc:
                raise ToolError("invalid_api_response") from exc
        if source_version not in {v.version for v in versions}:
            raise ToolError("unknown_version")
        return source_version

    def optimize(self, *, prompt_name: str, source_version: str, task_id: str,
                 candidate_version: str | None, train: Dataset, validation: Dataset) -> Mapping[str, Any]:
        try:
            body = OptimizationRequest.model_validate({"prompt_name": prompt_name,
                "prompt_version": source_version, "task_id": task_id, "candidate_version": candidate_version,
                "train": json_value(train), "validation": json_value(validation)}).model_dump(mode="json")
        except Exception as exc:
            raise ToolError("invalid_dataset") from exc
        value = self._request("POST", "/optimizations", missing="unknown_version", body=body)
        try:
            return validate_report(value).model_dump(mode="json")
        except Exception as exc:
            raise ToolError("invalid_api_response", state_requires_verification=True, outcome_unknown=True) from exc

    def candidate_status(self, name: str, version: str) -> str:
        versions = self._versions(name)
        for item in versions:
            if item.version == version:
                return item.status
        raise ToolError("invalid_api_response", state_requires_verification=True)

    def get_report(self, report_id: str) -> Mapping[str, Any]:
        value = self._request("GET", f"/optimizations/{segment(report_id)}", missing="report_not_found")
        try:
            result = validate_report(value)
            if result.id != report_id:
                raise ValueError("Report id mismatch")
            return result.model_dump(mode="json")
        except Exception as exc:
            raise ToolError("invalid_api_response") from exc
