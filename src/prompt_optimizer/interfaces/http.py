"""Synchronous HTTP boundary over shared application services."""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
from typing import Annotated
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from fastapi.responses import JSONResponse
from prompt_optimizer.application import EvaluatePrompt, OptimizePrompt, SaveCandidate, ApprovePrompt, RejectPrompt, PromotePrompt
from prompt_optimizer.application.reporting import build_report, json_value, write_report
from prompt_optimizer.domain import OptimizationTask
from prompt_optimizer.domain.registry import (RegistryError, VersionNotFoundError, DuplicateVersionError,
    InvalidTransitionError, RegistryConflictError, CorruptRegistryError)
from prompt_optimizer.ports import PromptRepository, ReportWriter
from prompt_optimizer.ports.optimization_results import OptimizationResultStore
from .http_schemas import (EvaluationRequest, OptimizationRequest, EvaluationReport,
    OptimizationResponse, OptimizationReport, PromptResponse, VersionResponse, ErrorResponse, LifecycleRequest)


@dataclass(frozen=True)
class HttpDependencies:
    repository: PromptRepository
    evaluation: EvaluatePrompt
    optimization: OptimizePrompt
    results: OptimizationResultStore
    report_writer: ReportWriter | None = None
    report_destination: str | None = None

    def __post_init__(self) -> None:
        if (self.report_writer is None) != (self.report_destination is None):
            raise ValueError("Report writer and server-configured destination must be paired")


def get_dependencies(request: Request) -> HttpDependencies:
    return request.app.state.dependencies


class WorkflowFailure(RuntimeError):
    def __init__(self, code: str, saved_candidate: dict | None = None) -> None:
        self.code, self.saved_candidate = code, saved_candidate
        super().__init__(code)


def error_response(status: int, code: str, *, saved: dict | None = None,
                   verify: bool = False) -> JSONResponse:
    messages = {
        "validation_error": "Request does not satisfy the HTTP contract.",
        "not_found": "Resource was not found.",
        "duplicate_identity": "Prompt version already exists.",
        "invalid_transition": "Lifecycle transition is not allowed.",
        "lock_conflict": "Registry is busy; inspect its state before retrying.",
        "corrupt_storage": "Registry requires operator inspection.",
        "registry_failure": "Registry operation failed; verify stored state before retrying.",
        "execution_failure": "Computation failed.",
        "result_storage_failure": "Result or report storage failed after candidate was saved.",
        "http_error": "HTTP request could not be handled.",
    }
    body = ErrorResponse.model_validate({"error": {"code": code, "message": messages[code],
        "saved_candidate": saved, "state_requires_verification": verify}})
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def create_app(dependencies: HttpDependencies) -> FastAPI:
    """Build an isolated app; no filesystem creation or server start on import."""
    app = FastAPI(title="Prompt Optimization Engine", version="0.1.0")
    app.state.dependencies = dependencies
    dep = Annotated[HttpDependencies, Depends(get_dependencies)]
    errors = {status: {"model": ErrorResponse} for status in (404, 409, 422, 500)}

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic input/ctx can contain credentials and nonfinite numbers.
        return error_response(422, "validation_error")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response(exc.status_code, "not_found" if exc.status_code == 404 else "http_error")

    @app.exception_handler(RegistryError)
    async def registry_error(request: Request, exc: RegistryError) -> JSONResponse:
        if isinstance(exc, VersionNotFoundError):
            return error_response(404, "not_found")
        for cls, code in ((DuplicateVersionError, "duplicate_identity"),
                          (InvalidTransitionError, "invalid_transition"),
                          (RegistryConflictError, "lock_conflict")):
            if isinstance(exc, cls):
                return error_response(409, code)
        if isinstance(exc, CorruptRegistryError):
            return error_response(500, "corrupt_storage", verify=True)
        return error_response(500, "registry_failure", verify=True)

    @app.exception_handler(WorkflowFailure)
    async def workflow_error(request: Request, exc: WorkflowFailure) -> JSONResponse:
        return error_response(500, exc.code, saved=exc.saved_candidate)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return error_response(500, "execution_failure", verify=True)

    def report(result, started, deps, schema):
        completed = datetime.now(timezone.utc)
        snapshot = schema.model_validate(build_report(result, started_at=started, completed_at=completed))
        if deps.report_writer is not None:
            write_report(result, deps.report_destination, writer=deps.report_writer,
                         started_at=started, completed_at=completed)
        return snapshot.model_dump(mode="json")

    @app.post("/evaluations", response_model=EvaluationReport, responses=errors)
    def evaluate(body: EvaluationRequest, deps: dep):
        started = datetime.now(timezone.utc)
        prompt = deps.repository.read(body.prompt_name, body.prompt_version)
        try:
            result = deps.evaluation.execute(prompt, body.dataset.domain())
            return report(result, started, deps, EvaluationReport)
        except Exception as exc:
            raise WorkflowFailure("execution_failure") from exc

    @app.post("/optimizations", response_model=OptimizationResponse, status_code=201, responses=errors)
    def optimize(body: OptimizationRequest, deps: dep):
        started = datetime.now(timezone.utc)
        source = deps.repository.read(body.prompt_name, body.prompt_version)
        version = body.candidate_version or deps.repository.next_version(body.prompt_name)
        task = OptimizationTask(body.task_id, source, body.train.domain().cases,
            body.validation.domain().cases, body.train.id, body.train.version)
        try:
            result = deps.optimization.execute(task, candidate_version=version)
        except Exception as exc:
            raise WorkflowFailure("execution_failure") from exc
        saved = SaveCandidate(deps.repository).execute(result)
        identity = {"name": saved.name, "version": saved.version, "status": saved.status.value}
        try:
            response = OptimizationResponse.model_validate({"id": str(uuid4()),
                "report": report(result, started, deps, OptimizationReport)}).model_dump(mode="json")
            deps.results.save(response["id"], response)
            return response
        except Exception as exc:
            raise WorkflowFailure("result_storage_failure", identity) from exc

    @app.get("/optimizations/{id}", response_model=OptimizationResponse, responses=errors)
    def retrieve(id: str, deps: dep):
        try:
            result = deps.results.get(id)
            if result is None:
                return error_response(404, "not_found")
            return OptimizationResponse.model_validate(result)
        except Exception as exc:
            raise WorkflowFailure("execution_failure") from exc

    @app.get("/prompts/{name}/versions", response_model=list[VersionResponse], responses=errors)
    def versions(name: str, deps: dep):
        prompts = deps.repository.list(name)
        if not prompts:
            return error_response(404, "not_found")
        return [{"version": p.version, "status": p.status.value, "parent_version": p.parent_version,
                 "provenance": json_value(deps.repository.provenance(name, p.version))} for p in prompts]

    @app.get("/prompts/{name}/production", response_model=PromptResponse, responses=errors)
    def production(name: str, deps: dep):
        prompt = deps.repository.production(name)
        if prompt is None:
            return error_response(404, "not_found")
        return json_value(prompt)

    @app.post("/prompts/{name}/{version}/approve", response_model=PromptResponse, responses=errors)
    def approve(name: str, version: str, deps: dep, body: LifecycleRequest | None = None):
        return json_value(ApprovePrompt(deps.repository).execute(name, version))

    @app.post("/prompts/{name}/{version}/reject", response_model=PromptResponse, responses=errors)
    def reject(name: str, version: str, deps: dep, body: LifecycleRequest | None = None):
        return json_value(RejectPrompt(deps.repository).execute(name, version))

    @app.post("/prompts/{name}/{version}/promote", response_model=PromptResponse, responses=errors)
    def promote(name: str, version: str, deps: dep, body: LifecycleRequest | None = None):
        return json_value(PromotePrompt(deps.repository).execute(name, version))

    return app
