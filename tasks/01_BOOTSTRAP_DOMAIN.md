# Session 01 --- Bootstrap + Domain

## Goal

Создать каркас проекта и чистые domain models.

## Implement

-   pyproject.toml;
-   package `src/prompt_optimizer`;
-   domain enums/models/exceptions;
-   базовый `__main__.py`;
-   pytest config;
-   README commands.

Минимальные модели: PromptVersion, PromptStatus, EvaluationCase,
MetricResult, EvaluationResult, OptimizationTask, OptimizationResult,
Recommendation.

## Tests

-   model creation/validation;
-   invalid statuses/data;
-   immutable version identity where appropriate.

## Acceptance

`pytest -q` проходит. `python -m prompt_optimizer --help` не падает
(может быть минимальным). Ни одного импорта DSPy в domain.
