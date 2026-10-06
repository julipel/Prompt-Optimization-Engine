"""Process-local, defensive snapshots; no persistence across restarts."""
from collections.abc import Mapping
from copy import deepcopy
from threading import Lock
from typing import Any
from prompt_optimizer.application.reporting import json_value


class InMemoryOptimizationResultStore:
    def __init__(self) -> None:
        self._results: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def save(self, identity: str, result: Mapping[str, Any]) -> None:
        snapshot = json_value(result)
        with self._lock:
            if identity in self._results:
                raise ValueError("Duplicate result identity")
            self._results[identity] = snapshot

    def get(self, identity: str) -> Mapping[str, Any] | None:
        with self._lock:
            return deepcopy(self._results.get(identity))
