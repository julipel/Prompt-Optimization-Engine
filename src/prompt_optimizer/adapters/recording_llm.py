"""Record a client's outputs for exact offline replay via ``prompt-opt --client fake``."""

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from prompt_optimizer.application.reporting import json_value
from prompt_optimizer.domain import PromptVersion
from prompt_optimizer.ports import LLMClient


class RecordingLLMClient:
    """Delegate to ``inner`` and keep ``{version: {case_id: output}}``.

    The saved file is the CLI ``--responses`` format, so a paid run can be
    replayed deterministically. A repeated (version, case) call is rejected:
    replay could not tell the two outputs apart.
    """

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.responses: dict[str, dict[str, Any]] = {}

    def generate(self, prompt: PromptVersion, *, case_id: str, input: str,
                 context: Mapping[str, Any]) -> Mapping[str, Any]:
        cases = self.responses.setdefault(prompt.version, {})
        if case_id in cases:
            raise ValueError(f"Case {case_id!r} already recorded for version {prompt.version!r}")
        output = self.inner.generate(prompt, case_id=case_id, input=input, context=context)
        cases[case_id] = json_value(output)
        return output

    def save(self, destination: str | Path) -> None:
        Path(destination).write_text(json.dumps(self.responses, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
