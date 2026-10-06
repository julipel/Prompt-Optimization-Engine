# Session 03 --- Evaluation engine

## Goal

Реализовать evaluation без GEPA.

## Implement

-   LLMClient port;
-   Evaluator port;
-   deterministic evaluators;
-   metric aggregation;
-   critical checks;
-   fake LLM adapter for tests;
-   EvaluatePrompt use case.

Не использовать LLM judge там, где достаточно structured comparison.

## Tests

price correctness, unknown price, tool selection, forbidden booking
confirmation, aggregate score, critical failure propagation.

## Acceptance

На demo dataset baseline evaluation создаёт детализированный
EvaluationResult.
