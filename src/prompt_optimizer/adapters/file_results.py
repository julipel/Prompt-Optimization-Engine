"""One JSON file per optimization result; survives service restarts."""
from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any
from prompt_optimizer.application.reporting import json_value

_IDENTITY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class FileOptimizationResultStore:
    """Only UUID identities map to files, so an id can never name another path.

    Writes are atomic and never overwrite: a duplicate identity is rejected.
    Unknown or malformed identities read as missing.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(os.path.abspath(root))

    def _path(self, identity: str) -> Path | None:
        if not isinstance(identity, str) or not _IDENTITY.fullmatch(identity):
            return None
        return self.root / f"{identity}.json"

    def save(self, identity: str, result: Mapping[str, Any]) -> None:
        path = self._path(identity)
        if path is None:
            raise ValueError("Result identity must be a lowercase UUID")
        payload = json.dumps(json_value(result), ensure_ascii=False, allow_nan=False)
        self.root.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.root, suffix=".tmp", delete=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(stream.name, path)  # Atomic and fails if the identity already exists.
        except FileExistsError as exc:
            raise ValueError("Duplicate result identity") from exc
        finally:
            os.unlink(stream.name)

    def get(self, identity: str) -> Mapping[str, Any] | None:
        path = self._path(identity)
        if path is None or not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
