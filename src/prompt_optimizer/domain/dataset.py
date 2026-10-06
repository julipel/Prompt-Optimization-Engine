"""Dataset identity and cases, independent of storage."""

from dataclasses import dataclass

from .exceptions import DomainValidationError
from .models import EvaluationCase, _text


@dataclass(frozen=True)
class Dataset:
    """Explicit versioned identity and a nonempty, ordered collection of cases."""

    id: str
    version: str
    cases: tuple[EvaluationCase, ...]

    def __post_init__(self) -> None:
        _text(self.id, "dataset id")
        _text(self.version, "dataset version")
        if not isinstance(self.cases, (list, tuple)) or not self.cases or not all(
            isinstance(case, EvaluationCase) for case in self.cases
        ):
            raise DomainValidationError("cases must contain nonempty evaluation cases")
        if len({case.id for case in self.cases}) != len(self.cases):
            raise DomainValidationError("Duplicate case ids in dataset")
        object.__setattr__(self, "cases", tuple(self.cases))
