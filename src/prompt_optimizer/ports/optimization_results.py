"""Small result snapshot port, separate from the prompt registry."""
from collections.abc import Mapping
from typing import Any, Protocol


class OptimizationResultStore(Protocol):
    def save(self, identity: str, result: Mapping[str, Any]) -> None: ...
    def get(self, identity: str) -> Mapping[str, Any] | None: ...
