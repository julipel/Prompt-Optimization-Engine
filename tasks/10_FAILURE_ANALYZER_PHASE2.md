# Session 10 --- Failure analyzer / continuous improvement (Phase 2)

## Goal

Подготовить controlled loop из production failures в evaluation dataset.

## Implement

-   FailureCase model;
-   import sanitized failure traces;
-   clustering/tagging interface;
-   draft evaluation case generation;
-   mandatory human review before dataset inclusion.

## Privacy

Не сохранять PII/PHI в demo или test fixtures. Предусмотреть
sanitization boundary.

## Flow

production trace -\> sanitize -\> analyze -\> draft case -\> human
review -\> dataset -\> optimization.

## Acceptance

Ни один production trace не попадает автоматически в trusted evaluation
dataset.
