"""Offline scripted client. Responses are supplied explicitly, never inferred."""

from collections.abc import Mapping
from typing import Any

from prompt_optimizer.domain import CaseEvaluationResult, PromptVersion


class FakeLLMClient:
    """Replay immutable structured responses keyed by case ID; record calls."""

    def __init__(self, responses: Mapping[str, Mapping[str, Any]], *,
                 version_responses: Mapping[tuple[str, str], Mapping[str, Any]] | None = None) -> None:
        self._responses = {key: CaseEvaluationResult(key, value, ()).output
                           for key, value in responses.items()}
        self.calls: list[tuple[PromptVersion, str, str, Mapping[str, Any]]] = []
        self._version_responses = {key: CaseEvaluationResult(key[1], value, ()).output
                                   for key, value in (version_responses or {}).items()}

    def generate(
        self, prompt: PromptVersion, *, case_id: str, input: str,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.calls.append((prompt, case_id, input, context))
        if (prompt.version, case_id) in self._version_responses:
            return self._version_responses[prompt.version, case_id]
        if case_id not in self._responses:
            raise KeyError(f"No fake response for case {case_id!r}")
        return self._responses[case_id]
