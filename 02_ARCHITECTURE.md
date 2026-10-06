# 02. Архитектура

## Layers

### domain

Чистые модели и правила: - PromptVersion - EvaluationCase -
EvaluationResult - MetricResult - OptimizationTask -
OptimizationResult - Recommendation - PromptStatus

### application

Use cases: - EvaluatePrompt - OptimizePrompt - CompareVersions -
PromotePrompt

### ports

Protocols: - LLMClient - PromptOptimizer - Evaluator -
PromptRepository - ReportWriter

### adapters

-   `dspy/gepa_optimizer.py`
-   LLM provider adapters
-   filesystem prompt registry
-   JSONL dataset loader
-   JSON report writer

### interfaces

MVP: CLI. Phase 2: FastAPI + agent tool.

## Dependency direction

interfaces -\> application -\> domain adapters implement ports
domain/application MUST NOT depend on DSPy.

## Optimization flow

1.  Resolve current prompt.
2.  Load train + validation datasets.
3.  Evaluate baseline on validation.
4.  Optimize using train dataset.
5.  Persist candidate as new immutable version.
6.  Evaluate candidate on validation.
7.  Compare metrics.
8.  Apply hard constraints/regression gates.
9.  Generate report.
10. Return recommendation.
11. Stop. No automatic production promotion.

## Evaluator strategy

Deterministic first: - exact/structured values; - expected tool; -
forbidden claims; - required action.

LLM judge only for semantic qualities not reliably expressible as code.

## Future extension

`PromptOptimizer` can later have: GEPAOptimizer, MIPROOptimizer,
SIMBAOptimizer, ManualOptimizer.
