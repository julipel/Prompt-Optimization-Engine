# Session 04 --- DSPy + GEPA adapter

## Goal

Подключить GEPA за портом PromptOptimizer, не загрязняя core.

## Before coding

Проверь актуальный API установленной версии DSPy/GEPA. Не придумывай
сигнатуры. Зафиксируй совместимые версии зависимостей.

## Implement

-   PromptOptimizer protocol if not yet present;
-   DSPy configuration isolated in adapter;
-   GEPAOptimizer;
-   mapping domain task/dataset -\> DSPy representation;
-   mapping optimizer output -\> domain result;
-   clear errors for missing provider credentials/config.

## Tests

Unit tests mock DSPy boundary. Optional marked smoke test with real
provider credentials.

## Acceptance

Application layer использует только PromptOptimizer port. Замена fake
optimizer на GEPA не требует изменения use case.
