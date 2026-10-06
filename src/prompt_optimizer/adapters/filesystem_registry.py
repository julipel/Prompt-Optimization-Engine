"""One atomic, validated snapshot per prompt; fail-fast interprocess locking."""
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile

from prompt_optimizer.domain import PromptVersion, PromptStatus
from prompt_optimizer.domain.registry import (
    RegistryError, VersionNotFoundError, DuplicateVersionError, CorruptRegistryError,
    RegistryConflictError, InvalidTransitionError, RegistryProvenance, StatusTransition, transition,
)


def _component(value: str) -> str:
    if (not isinstance(value, str) or not value or value in (".", "..")
        or value[-1] in " ." or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in value)
        or re.match(r"^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|CLOCK\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", value, re.I)):
        raise ValueError("Unsafe filesystem identifier")
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _aware(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    return result


class FilesystemPromptRepository:
    def __init__(self, root: str | Path) -> None:
        # Capture an absolute lexical path; do not resolve away junctions.
        self.root = Path(os.path.abspath(root))

    def _safe(self, path: Path):
        for item in (path, *path.parents):
            if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
                raise ValueError("Symlinks and junctions are forbidden in registry paths")
            if item.exists() and getattr(item.stat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("Reparse points are forbidden")

    @contextmanager
    def _access(self, operation, name, version=None):
        lock = None
        acquired = False
        try:
            _component(name)
            if version is not None:
                _component(version)
            self._safe(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            directory = self.root / "prompts"
            self._safe(directory)
            directory.mkdir(exist_ok=True)
            # Canonical casefold paths make collisions detectable on all platforms.
            path = directory / (name.casefold() + ".json")
            lock = directory / (name.casefold() + ".lock")
            self._safe(path)
            self._safe(lock)
            try:
                lock.mkdir()
            except FileExistsError as exc:
                raise RegistryConflictError(operation, name, version, "prompt is locked") from exc
            acquired = True
            yield path
        except RegistryError:
            raise
        except Exception as exc:
            raise RegistryError(operation, name, version, str(exc)) from exc
        finally:
            if acquired:
                try:
                    lock.rmdir()
                except OSError as exc:
                    raise RegistryError(operation, name, version, "lock cleanup failed; commit may have succeeded") from exc

    def _load(self, path, operation, name, version=None):
        if not path.exists():
            return {"schema": 1, "name": name, "records": {}, "history": []}
        try:
            state = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs,
                               parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
            self._validate(state, name)
            return state
        except OSError as exc:
            raise RegistryError(operation, name, version, "read failed") from exc
        except Exception as exc:
            raise CorruptRegistryError(operation, name, version, "invalid snapshot: " + str(exc)) from exc

    def _decode(self, record):
        if set(record) != {"prompt", "provenance"}:
            raise ValueError("Invalid record fields")
        data = dict(record["prompt"])
        if set(data) != {f.name for f in fields(PromptVersion)}:
            raise ValueError("Incomplete prompt metadata")
        data["created_at"] = _aware(data["created_at"])
        prompt = PromptVersion(**data)
        if set(record["provenance"]) != {"task_id", "metadata"}:
            raise ValueError("Invalid provenance fields")
        RegistryProvenance(**record["provenance"])
        return prompt

    def _validate(self, state, name):
        if set(state) != {"schema", "name", "records", "history"} or type(state["schema"]) is not int or state["schema"] != 1:
            raise ValueError("Unsupported schema")
        if state["name"] != name:
            raise ValueError("Prompt name/case collision")
        if not isinstance(state["records"], dict) or not isinstance(state["history"], list):
            raise ValueError("Invalid snapshot types")
        prompts = {}
        for version, record in state["records"].items():
            _component(version)
            if version.casefold() in {v.casefold() for v in prompts}:
                raise ValueError("Version case collision")
            prompt = self._decode(record)
            if (prompt.name, prompt.version) != (name, version):
                raise ValueError("Identity mismatch")
            prompts[version] = prompt
        statuses = {}
        pending_archive = None
        last_at = None
        for event in state["history"]:
            if set(event) != {"operation", "version", "previous", "status", "at"}:
                raise ValueError("Invalid history fields")
            at = _aware(event["at"])
            if last_at is not None and at < last_at:
                raise ValueError("History timestamps are out of order")
            last_at = at
            v, op = event["version"], event["operation"]
            if v not in prompts:
                raise ValueError("History references missing version")
            previous = statuses.get(v)
            if event["previous"] != previous:
                raise ValueError("History previous status mismatch")
            status = PromptStatus(event["status"])
            if pending_archive is not None and op != "promote":
                raise ValueError("Archive must be paired with promotion")
            if op in ("create", "bootstrap"):
                if previous is not None or (op == "bootstrap" and statuses):
                    raise ValueError("Invalid creation history")
                expected = PromptStatus.CANDIDATE if op == "create" else PromptStatus.PRODUCTION
                parent = prompts[v].parent_version
                if parent is not None and parent not in statuses:
                    raise ValueError("Parent must predate child")
                if op == "bootstrap" and parent is not None:
                    raise ValueError("Bootstrap cannot have parent")
            else:
                expected = transition(previous, op)
                if op == "archive":
                    pending_archive = v
                if op == "promote":
                    if any(s is PromptStatus.PRODUCTION for s in statuses.values()):
                        raise ValueError("Multiple production versions")
                    pending_archive = None
            if status != expected:
                raise ValueError("Invalid resulting status")
            statuses[v] = status
        if pending_archive is not None or set(statuses) != set(prompts):
            raise ValueError("Incomplete history")
        if any(p.status != statuses[v] for v, p in prompts.items()):
            raise ValueError("Snapshot/history mismatch")

    def _write(self, path, state):
        self._validate(state, state["name"])
        payload = json.dumps(state, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".registry-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _event(self, state, prompt, op, previous):
        at = datetime.now(timezone.utc)
        if state["history"]:
            at = max(at, _aware(state["history"][-1]["at"]))
        state["history"].append(dict(operation=op, version=prompt.version, previous=previous,
                                    status=prompt.status.value, at=at.isoformat()))

    def _create(self, prompt, provenance, bootstrap):
        op = "bootstrap" if bootstrap else "create"
        with self._access(op, prompt.name, prompt.version) as path:
            state = self._load(path, op, prompt.name, prompt.version)
            records = state["records"]
            if prompt.version.casefold() in {v.casefold() for v in records}:
                raise DuplicateVersionError(op, prompt.name, prompt.version, "version already exists")
            if prompt.status is not (PromptStatus.PRODUCTION if bootstrap else PromptStatus.CANDIDATE):
                raise ValueError("create requires candidate; bootstrap requires production")
            if bootstrap and (records or prompt.parent_version is not None):
                raise ValueError("bootstrap requires empty prompt history and no parent")
            if prompt.parent_version is not None and prompt.parent_version not in records:
                raise ValueError("Parent does not exist for this prompt")
            provenance = provenance if provenance is not None else RegistryProvenance()
            if not isinstance(provenance, RegistryProvenance):
                raise ValueError("Invalid registry provenance")
            records[prompt.version] = {"prompt": {f.name: _plain(getattr(prompt, f.name)) for f in fields(prompt)},
                                       "provenance": dict(task_id=provenance.task_id, metadata=_plain(provenance.metadata))}
            self._event(state, prompt, op, None)
            self._write(path, state)
            return self._decode(records[prompt.version])

    def create(self, prompt: PromptVersion, *, provenance: RegistryProvenance | None = None) -> PromptVersion:
        return self._create(prompt, provenance, False)

    def bootstrap(self, prompt: PromptVersion) -> PromptVersion:
        return self._create(prompt, None, True)

    def _record(self, state, operation, name, version):
        try:
            return state["records"][version]
        except KeyError as exc:
            raise VersionNotFoundError(operation, name, version, "version not found") from exc

    def read(self, name: str, version: str) -> PromptVersion:
        with self._access("read", name, version) as path:
            state = self._load(path, "read", name, version)
            return self._decode(self._record(state, "read", name, version))

    def list(self, name: str) -> tuple[PromptVersion, ...]:
        with self._access("list", name) as path:
            state = self._load(path, "list", name)
            return tuple(self._decode(state["records"][v]) for v in sorted(state["records"]))

    def production(self, name: str) -> PromptVersion | None:
        return next((p for p in self.list(name) if p.status is PromptStatus.PRODUCTION), None)

    def next_version(self, name: str) -> str:
        with self._access("next_version", name) as path:
            state = self._load(path, "next_version", name)
            numbers = []
            for v in state["records"]:
                if not re.fullmatch(r"v[0-9]{3,}", v) or int(v[1:]) < 1 or v != f"v{int(v[1:]):03d}":
                    raise ValueError("Unsupported version format: " + v)
                numbers.append(int(v[1:]))
            return f"v{max(numbers, default=0) + 1:03d}"

    def _transition(self, name, version, operation):
        with self._access(operation, name, version) as path:
            state = self._load(path, operation, name, version)
            record = self._record(state, operation, name, version)
            prompt = self._decode(record)
            try:
                status = transition(prompt.status, operation)
            except ValueError as exc:
                raise InvalidTransitionError(operation, name, version, str(exc)) from exc
            if operation == "promote":
                for other in state["records"].values():
                    previous = self._decode(other)
                    if previous.status is PromptStatus.PRODUCTION:
                        archived = replace(previous, status=PromptStatus.ARCHIVED)
                        other["prompt"]["status"] = archived.status.value
                        self._event(state, archived, "archive", previous.status.value)
            updated = replace(prompt, status=status)
            record["prompt"]["status"] = status.value
            self._event(state, updated, operation, prompt.status.value)
            self._write(path, state)
            return updated

    def approve(self, name: str, version: str) -> PromptVersion:
        return self._transition(name, version, "approve")

    def reject(self, name: str, version: str) -> PromptVersion:
        return self._transition(name, version, "reject")

    def promote(self, name: str, version: str) -> PromptVersion:
        return self._transition(name, version, "promote")

    def history(self, name: str) -> tuple[StatusTransition, ...]:
        with self._access("history", name) as path:
            state = self._load(path, "history", name)
            return tuple(StatusTransition(e["operation"], e["version"],
                PromptStatus(e["previous"]) if e["previous"] is not None else None,
                PromptStatus(e["status"]), _aware(e["at"])) for e in state["history"])

    def provenance(self, name: str, version: str) -> RegistryProvenance:
        with self._access("provenance", name, version) as path:
            state = self._load(path, "provenance", name, version)
            return RegistryProvenance(**self._record(state, "provenance", name, version)["provenance"])
