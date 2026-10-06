"""Server-configured dataset identity mapping, never agent-controlled paths."""
from collections.abc import Mapping
from prompt_optimizer.application.datasets import load_train_validation
from prompt_optimizer.domain import Dataset
from prompt_optimizer.ports import DatasetLoader
from prompt_optimizer.ports.agent_tool import ToolError


class FileDatasetResolver:
    def __init__(self, sources: Mapping[tuple[str, str], tuple[str, str]], loader: DatasetLoader) -> None:
        self._sources = dict(sources)
        self._loader = loader

    def resolve(self, dataset_id: str, dataset_version: str) -> tuple[Dataset, Dataset]:
        try:
            train, validation = self._sources[dataset_id, dataset_version]
        except KeyError as exc:
            raise ToolError("unknown_dataset") from exc
        try:
            return load_train_validation(train, validation, dataset_id=dataset_id,
                                         version=dataset_version, loader=self._loader)
        except Exception as exc:
            raise ToolError("invalid_dataset") from exc
