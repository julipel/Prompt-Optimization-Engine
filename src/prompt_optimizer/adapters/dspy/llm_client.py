"""Real-provider LLMClient on the same DSPy signature GEPA optimizes against."""

from collections.abc import Mapping
from math import isfinite
import re
from typing import Any

import dspy

from prompt_optimizer.domain import PromptVersion
from prompt_optimizer.adapters.dspy.gepa_optimizer import (GEPAConfigurationError, _credential, _json,
                                                           structured_signature)


class LLMGenerationError(RuntimeError):
    """Provider call or output parsing failed; provider text is withheld."""


class DSPyLLMClient:
    """Evaluate a prompt version with a real model; one call per case, no cache.

    Instructions are applied exactly as GEPA applies candidate text, so a GEPA
    candidate scores the same way here as during optimization. The API key is
    read from ``credential_env`` at call time and never stored. ``lm`` injects a
    ready DSPy LM (e.g. ``DummyLM`` in tests) instead of building one. Pass the
    same ``output_description`` as ``GEPAConfig`` (None keeps the clinic hint).
    """

    def __init__(self, model: str, credential_env: str, *, max_tokens: int = 16000,
                 temperature: float = 1.0, num_retries: int = 0, api_base: str | None = None,
                 output_description: str | None = None, lm: Any | None = None) -> None:
        if not isinstance(model, str) or not re.fullmatch(r"[^\s/]+/[^\s]+", model):
            raise GEPAConfigurationError("model must use provider/model notation")
        if not isinstance(credential_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", credential_env):
            raise GEPAConfigurationError("credential_env must name an environment variable")
        if type(max_tokens) is not int or max_tokens <= 0:
            raise GEPAConfigurationError("max_tokens must be a positive integer")
        if type(num_retries) is not int or num_retries < 0:
            raise GEPAConfigurationError("num_retries must be a nonnegative integer")
        if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                or not isfinite(temperature) or not 0 <= temperature <= 2):
            raise GEPAConfigurationError("temperature must be finite and in [0, 2]")
        if output_description is not None and (not isinstance(output_description, str)
                                               or not output_description.strip()):
            raise GEPAConfigurationError("output_description must be a nonempty string")
        self.model = model
        self._output_description = output_description
        self._credential_env = credential_env
        self._settings = dict(max_tokens=max_tokens, temperature=temperature, num_retries=num_retries,
                              api_base=api_base)
        self._lm = lm

    def _language_model(self) -> Any:
        if self._lm is not None:
            return self._lm
        return dspy.LM(self.model, api_key=_credential(self._credential_env), cache=False, **self._settings)

    def generate(self, prompt: PromptVersion, *, case_id: str, input: str,
                 context: Mapping[str, Any]) -> Mapping[str, Any]:
        lm = self._language_model()
        try:
            with dspy.context(lm=lm, adapter=dspy.JSONAdapter()):
                prediction = dspy.Predict(structured_signature(prompt.text, self._output_description))(
                    input=input, context=_json(context))
            output = prediction.output
        except Exception as exc:
            # Provider exception text may echo request data or credentials.
            raise LLMGenerationError(f"Generation failed for case {case_id!r}") from exc
        if not isinstance(output, Mapping):
            raise LLMGenerationError(f"Case {case_id!r}: model output is not a JSON object")
        return dict(output)
