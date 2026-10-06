"""Atomic UTF-8 report replacement."""
import json
import os
from pathlib import Path
import tempfile
from collections.abc import Mapping
from typing import Any
from prompt_optimizer.application.reporting import json_value


class ReportWriteError(RuntimeError):
    """Serialization/storage failure with original cause."""


class JsonReportWriter:
    def write(self, report: Mapping[str, Any], destination: str) -> None:
        temporary = None
        try:
            payload = json.dumps(json_value(report), ensure_ascii=False, allow_nan=False, indent=2)
            path = Path(destination)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                    prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                stream.write(payload + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except Exception as exc:
            raise ReportWriteError("Report write failed") from exc
        finally:
            if temporary is not None and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
