"""Registry contracts and lifecycle rules, without persistence dependencies."""
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .models import PromptStatus, _mapping, _text


class RegistryError(RuntimeError):
    def __init__(self, operation: str, name: str, version: str | None, reason: str) -> None:
        self.operation, self.name, self.version, self.reason = operation, name, version, reason
        super().__init__(f"Registry {operation} {name}/{version or '*'}: {reason}")


class VersionNotFoundError(RegistryError):
    pass


class DuplicateVersionError(RegistryError):
    pass


class CorruptRegistryError(RegistryError):
    pass


class RegistryConflictError(RegistryError):
    pass


class InvalidTransitionError(RegistryError):
    pass


@dataclass(frozen=True)
class RegistryProvenance:
    task_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.task_id is not None:
            _text(self.task_id, "task_id")
        object.__setattr__(self, "metadata", _mapping(self.metadata, "metadata"))
        def check(value):
            if isinstance(value, Mapping):
                for key, item in value.items():
                    normalized = key.lower().replace("-", "_")
                    if any(word in normalized for word in ("secret", "password", "credential", "api_key", "access_token", "authorization")):
                        raise ValueError("Secret-bearing metadata keys are forbidden")
                    check(item)
            elif isinstance(value, tuple):
                for item in value:
                    check(item)
        check(self.metadata)


@dataclass(frozen=True)
class StatusTransition:
    operation: str
    version: str
    previous: PromptStatus | None
    status: PromptStatus
    at: datetime


def transition(status: PromptStatus, operation: str) -> PromptStatus:
    rules = {
        (PromptStatus.CANDIDATE, "approve"): PromptStatus.APPROVED,
        (PromptStatus.CANDIDATE, "reject"): PromptStatus.REJECTED,
        (PromptStatus.APPROVED, "promote"): PromptStatus.PRODUCTION,
        (PromptStatus.PRODUCTION, "archive"): PromptStatus.ARCHIVED,
    }
    try:
        return rules[status, operation]
    except KeyError as exc:
        raise ValueError(f"Cannot {operation} {status.value}") from exc
