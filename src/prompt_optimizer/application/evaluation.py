"""Run evaluation with explicit client and evaluator dependencies."""

from collections.abc import Sequence

from prompt_optimizer.domain import CaseEvaluationResult, Dataset, EvaluationResult, PromptVersion
from prompt_optimizer.domain.evaluation import aggregate_metrics
from prompt_optimizer.ports import Evaluator, LLMClient


class EvaluationError(RuntimeError):
    """An execution/contract failure, distinct from a scored model failure."""

    def __init__(self, case_id: str, stage: str) -> None:
        self.case_id = case_id
        self.stage = stage
        super().__init__(f"Evaluation failed for case {case_id!r} during {stage}")


class EvaluatePrompt:
    """Sequential reproducible evaluation; abort on infrastructure/contract errors."""

    def __init__(self, client: LLMClient, evaluators: Sequence[Evaluator]) -> None:
        self.client = client
        self.evaluators = tuple(evaluators)
        if not self.evaluators:
            raise ValueError("At least one evaluator is required")

    def execute(self, prompt: PromptVersion, dataset: Dataset) -> EvaluationResult:
        results: list[CaseEvaluationResult] = []
        for case in dataset.cases:
            try:
                output = self.client.generate(
                    prompt, case_id=case.id, input=case.input, context=case.context,
                )
                # Validate and freeze JSON before any evaluator sees it.
                snapshot = CaseEvaluationResult(case.id, output, ())
            except Exception as exc:
                raise EvaluationError(case.id, "generation") from exc
            try:
                metrics = tuple(metric for evaluator in self.evaluators
                                for metric in evaluator.evaluate(case, snapshot.output))
                result = CaseEvaluationResult(case.id, snapshot.output, metrics)
                aggregate_metrics((result,))  # Enforce nonempty/reserved-name contracts.
            except Exception as exc:
                raise EvaluationError(case.id, "evaluation") from exc
            results.append(result)
        try:
            scores = aggregate_metrics(results)
        except Exception as exc:
            raise EvaluationError(results[-1].case_id, "aggregation") from exc
        return EvaluationResult(prompt.name, prompt.version, dataset.id, dataset.version,
                                tuple(results), scores)
