"""Deterministic comparison of validation evaluations, without model judges."""

from decimal import Decimal

from prompt_optimizer.domain import EvaluationResult, Recommendation
from prompt_optimizer.domain.comparison import ComparisonResult, GateResult, RegressionThresholds
from prompt_optimizer.domain.evaluation import aggregate_metrics


def _signature(result: EvaluationResult, name: str) -> tuple:
    names = {name} if name != "aggregate" else {
        m.name for c in result.cases for m in c.metrics if not m.critical
    }
    return tuple(sorted((c.case_id, m.name, m.critical) for c in result.cases
                        for m in c.metrics if m.name in names))


class CompareVersions:
    def __init__(self, thresholds: RegressionThresholds | None = None) -> None:
        self.thresholds = thresholds if thresholds is not None else RegressionThresholds()
        if not isinstance(self.thresholds, RegressionThresholds):
            raise ValueError("thresholds must be RegressionThresholds")

    def execute(self, baseline: EvaluationResult, candidate: EvaluationResult) -> ComparisonResult:
        if (baseline.prompt_name, baseline.dataset_id, baseline.dataset_version) != (
            candidate.prompt_name, candidate.dataset_id, candidate.dataset_version
        ) or {c.case_id for c in baseline.cases} != {c.case_id for c in candidate.cases}:
            raise ValueError("Comparisons require the same prompt name and validation dataset/case ids")
        # Reject fabricated summary scores rather than comparing inconsistent reports.
        for result in (baseline, candidate):
            if dict(aggregate_metrics(result.cases)) != dict(result.scores):
                raise ValueError("Evaluation scores do not match detailed metrics")
        gates = [GateResult("critical_failures", not bool(candidate.critical_failures),
                            f"Candidate critical failures: {dict(candidate.critical_failures)}")]
        names = sorted(set(baseline.scores) | set(candidate.scores) |
                       set(self.thresholds.allowed_decrease) | set(self.thresholds.minimum_scores))
        deltas = {}
        incomplete = False
        rejected = bool(candidate.critical_failures)
        for name in names:
            comparable = name in baseline.scores and name in candidate.scores and (
                _signature(baseline, name) == _signature(candidate, name)
            )
            gates.append(GateResult(f"comparable:{name}", comparable,
                                    "Same metric coverage and critical semantics" if comparable else
                                    "Missing metric or changed case coverage/critical semantics"))
            if not comparable:
                deltas[name] = None
                incomplete = True
            else:
                delta = Decimal(str(candidate.scores[name])) - Decimal(str(baseline.scores[name]))
                deltas[name] = float(delta)
                limit = self.thresholds.allowed_decrease.get(name, 0.0)
                passed = delta >= -Decimal(str(limit))
                rejected |= not passed
                gates.append(GateResult(f"regression:{name}", passed,
                                        f"delta={delta}; allowed decrease={limit} (inclusive)"))
            if name in self.thresholds.minimum_scores:
                minimum = self.thresholds.minimum_scores[name]
                passed = name in candidate.scores and candidate.scores[name] >= minimum
                rejected |= not passed
                gates.append(GateResult(f"minimum:{name}", passed,
                                        f"candidate={candidate.scores.get(name)}; minimum={minimum} (inclusive)"))
        improved = deltas.get("aggregate") is not None and deltas["aggregate"] > 0
        recommendation = (Recommendation.REJECT if rejected else Recommendation.APPROVE
                          if improved and not incomplete else Recommendation.REVIEW)
        gates.append(GateResult("improvement", improved,
                                "Approval requires strictly positive comparable aggregate delta"))
        return ComparisonResult(baseline, candidate, deltas, tuple(gates), recommendation)
