"""End-to-end validation pipeline; recommendations never promote candidates."""

from prompt_optimizer.domain import Dataset, EvaluationResult, OptimizationResult, OptimizationTask, PromptStatus, PromptVersion
from prompt_optimizer.domain import OptimizationPipelineResult, RegressionThresholds
from prompt_optimizer.ports import PromptOptimizer
from .evaluation import EvaluatePrompt
from .comparison import CompareVersions


class OptimizationError(RuntimeError):
    """Optimizer execution or external result contract failed."""


class PipelineError(RuntimeError):
    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(f"Optimization pipeline failed during {stage}")


class OptimizePrompt:
    def __init__(self, optimizer: PromptOptimizer, evaluation: EvaluatePrompt,
                 thresholds: RegressionThresholds | None = None) -> None:
        self.optimizer = optimizer
        self.evaluation = evaluation
        self.comparison = CompareVersions(thresholds)

    def execute(self, task: OptimizationTask, *, candidate_version: str) -> OptimizationPipelineResult:
        if not isinstance(task, OptimizationTask):
            raise ValueError("task must be OptimizationTask")
        if not isinstance(candidate_version, str) or not candidate_version.strip() or candidate_version == task.source_prompt.version:
            raise ValueError("candidate_version must be nonempty and differ from source version")
        validation = Dataset(task.dataset_id, task.dataset_version, task.validation_cases)
        try:
            baseline = self.evaluation.execute(task.source_prompt, validation)
            self._validate_evaluation(baseline, task.source_prompt, validation)
        except Exception as exc:
            raise PipelineError("baseline_evaluation") from exc
        try:
            optimized = self.optimizer.optimize(task, candidate_version=candidate_version)
        except Exception as exc:
            raise PipelineError("optimizer") from exc
        try:
            if not isinstance(optimized, OptimizationResult):
                raise ValueError("optimizer must return OptimizationResult")
            candidate = optimized.candidate
            expected = dict(name=task.source_prompt.name, version=candidate_version,
                            status=PromptStatus.CANDIDATE, parent_version=task.source_prompt.version,
                            optimizer=optimized.optimizer, dataset_id=task.dataset_id,
                            dataset_version=task.dataset_version)
            if optimized.task_id != task.task_id:
                raise ValueError("optimizer task_id mismatch")
            for name, value in expected.items():
                if getattr(candidate, name) != value:
                    raise ValueError(f"optimizer candidate {name} mismatch")
        except Exception as exc:
            raise PipelineError("optimizer_contract") from exc
        try:
            after = self.evaluation.execute(candidate, validation)
            self._validate_evaluation(after, candidate, validation)
        except Exception as exc:
            raise PipelineError("candidate_evaluation") from exc
        try:
            comparison = self.comparison.execute(baseline, after)
            return OptimizationPipelineResult(task.task_id, optimized, comparison)
        except Exception as exc:
            raise PipelineError("comparison") from exc

    @staticmethod
    def _validate_evaluation(result: EvaluationResult, prompt: PromptVersion, dataset: Dataset) -> None:
        if not isinstance(result, EvaluationResult) or (
            result.prompt_name, result.prompt_version, result.dataset_id, result.dataset_version,
            tuple(c.case_id for c in result.cases)
        ) != (prompt.name, prompt.version, dataset.id, dataset.version, tuple(c.id for c in dataset.cases)):
            raise ValueError("evaluation result identity/case ids mismatch")
