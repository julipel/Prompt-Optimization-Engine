"""Paid real-provider run behind docs/CASE_STUDY.md: baseline -> GEPA -> candidate -> test.

GEPA trains on train and selects on validation; both versions are then scored
on the held-out test split, which neither the optimizer nor selection saw.

Every model output used in the report is recorded to responses.json, so the
exact result replays offline for free via ``prompt-opt ... --client fake``
(see docs/CASE_STUDY.md). Requires --yes-paid-run plus OPENAI_API_KEY,
GEPA_MODEL and GEPA_REFLECTION_MODEL in the environment.

    set -a; source .env; set +a
    python examples/clinic_case_study.py --yes-paid-run
"""
import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from prompt_optimizer.adapters.dspy.gepa_optimizer import GEPAConfig, GEPAOptimizer
from prompt_optimizer.adapters.dspy.llm_client import DSPyLLMClient
from prompt_optimizer.adapters.filesystem_registry import FilesystemPromptRepository
from prompt_optimizer.adapters.json_report import JsonReportWriter
from prompt_optimizer.adapters.jsonl_dataset import JsonlDatasetLoader
from prompt_optimizer.adapters.recording_llm import RecordingLLMClient
from prompt_optimizer.application import EvaluatePrompt, OptimizePrompt
from prompt_optimizer.application.datasets import load_dataset, load_train_validation
from prompt_optimizer.application.registry import SaveCandidate
from prompt_optimizer.application.reporting import terminal_summary, write_report
from prompt_optimizer.domain import OptimizationTask, PromptVersion
from prompt_optimizer.domain.evaluation import DeterministicEvaluator

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets" / "clinic_demo"
DATASET_ID, DATASET_VERSION = "clinic-demo", "2"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes-paid-run", action="store_true", help="confirm real, billed provider calls")
    parser.add_argument("--out", default=str(ROOT / "docs" / "case_study"))
    parser.add_argument("--max-metric-calls", type=int, default=150)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    model, reflection_model = os.environ.get("GEPA_MODEL"), os.environ.get("GEPA_REFLECTION_MODEL")
    if not args.yes_paid_run or not model or not reflection_model:
        parser.error("needs --yes-paid-run and GEPA_MODEL/GEPA_REFLECTION_MODEL in the environment")
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty; refusing to overwrite a recorded run")
    max_tokens = int(os.environ.get("GEPA_MAX_TOKENS", "16000"))

    baseline_text = (DATA / "baseline_prompt.txt").read_text(encoding="utf-8")
    train, validation = load_train_validation(str(DATA / "train.jsonl"), str(DATA / "validation.jsonl"),
        dataset_id=DATASET_ID, version=DATASET_VERSION, loader=JsonlDatasetLoader())
    test = load_dataset(str(DATA / "test.jsonl"), dataset_id=DATASET_ID, version=DATASET_VERSION,
                        loader=JsonlDatasetLoader())
    recorder = RecordingLLMClient(DSPyLLMClient(model, "OPENAI_API_KEY", max_tokens=max_tokens, num_retries=2))
    optimizer = GEPAOptimizer(GEPAConfig(model, reflection_model, "OPENAI_API_KEY", max_tokens=max_tokens,
                                         max_metric_calls=args.max_metric_calls, seed=args.seed))

    with TemporaryDirectory(prefix="clinic-case-study-") as directory:
        repository = FilesystemPromptRepository(Path(directory) / "registry")
        source = repository.bootstrap(PromptVersion("clinic", "v001", baseline_text, status="production"))
        task = OptimizationTask("clinic-case-study", source, train.cases, validation.cases,
                                DATASET_ID, DATASET_VERSION)
        # Default thresholds on purpose: the CLI replay uses the same ones.
        started = datetime.now(timezone.utc)
        evaluation = EvaluatePrompt(recorder, [DeterministicEvaluator()])
        result = OptimizePrompt(optimizer, evaluation).execute(task, candidate_version="v002")
        completed = datetime.now(timezone.utc)
        saved = SaveCandidate(repository).execute(result)
        test_runs = []
        for prompt in (source, saved):
            test_started = datetime.now(timezone.utc)
            test_runs.append((prompt.version, evaluation.execute(prompt, test), test_started,
                              datetime.now(timezone.utc)))

    out.mkdir(parents=True, exist_ok=True)
    write_report(result, str(out / "report.json"), writer=JsonReportWriter(),
                 started_at=started, completed_at=completed)
    for prompt_version, test_result, test_started, test_completed in test_runs:
        write_report(test_result, str(out / f"test_{prompt_version}.json"), writer=JsonReportWriter(),
                     started_at=test_started, completed_at=test_completed)
    recorder.save(out / "responses.json")
    (out / "candidate_prompt.txt").write_text(result.candidate.text, encoding="utf-8")
    metadata = {"model": model, "reflection_model": reflection_model, "max_tokens": max_tokens,
                "max_metric_calls": args.max_metric_calls, "seed": args.seed,
                "dspy_version": version("dspy"), "gepa_version": version("gepa"),
                "dataset": f"{DATASET_ID}@{DATASET_VERSION}", "train_cases": len(train.cases),
                "validation_cases": len(validation.cases), "test_cases": len(test.cases), "started_at": started.isoformat(),
                "completed_at": completed.isoformat()}
    (out / "run_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    print(terminal_summary(result))
    for prompt_version, test_result, _, _ in test_runs:
        print(f"Test {prompt_version}: {terminal_summary(test_result)}")
    print(f"Saved {saved.name}/{saved.version} as {saved.status.value}; artifacts in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
