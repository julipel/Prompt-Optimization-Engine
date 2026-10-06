"""Dataset loading with an explicit infrastructure dependency."""

from prompt_optimizer.domain import Dataset
from prompt_optimizer.ports import DatasetLoader


class DatasetLoadError(ValueError):
    """Source, physical line and case identity for a dataset loading failure."""

    def __init__(
        self, source: str, reason: str, *, line: int | None = None,
        case_id: str | None = None,
    ) -> None:
        self.source = source
        self.line = line
        self.case_id = case_id
        self.reason = reason
        location = source
        if line is not None:
            location += f":{line}"
        if case_id is not None:
            location += f" (case {case_id!r})"
        super().__init__(f"{location}: {reason}")


def load_dataset(
    source: str, *, dataset_id: str, version: str, loader: DatasetLoader,
) -> Dataset:
    """Load one dataset through the supplied port; no filesystem assumptions."""
    return loader.load(source, dataset_id=dataset_id, version=version)


def load_train_validation(
    train_source: str, validation_source: str, *, dataset_id: str,
    version: str, loader: DatasetLoader,
) -> tuple[Dataset, Dataset]:
    """Load explicit train and validation files for the same dataset release.

    No automatic split or shuffle is performed. IDs must be unique within
    each split; the same ID may occur in both splits.
    """
    train = load_dataset(train_source, dataset_id=dataset_id, version=version, loader=loader)
    validation = load_dataset(
        validation_source, dataset_id=dataset_id, version=version, loader=loader,
    )
    return train, validation
