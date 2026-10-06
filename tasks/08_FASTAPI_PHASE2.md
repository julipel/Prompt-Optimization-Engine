# Session 08 --- FastAPI (Phase 2)

## Goal

Обернуть готовые application use cases HTTP API без дублирования
бизнес-логики.

## Endpoints

POST /evaluations POST /optimizations GET /optimizations/{id} GET
/prompts/{name}/versions POST /prompts/{name}/{version}/approve POST
/prompts/{name}/{version}/reject POST /prompts/{name}/{version}/promote

## Requirements

Pydantic request/response schemas на boundary. API не обращается
напрямую к DSPy или filesystem.

## Tests

FastAPI TestClient, validation, success/error status codes.

## Acceptance

CLI и API используют одни application services.
