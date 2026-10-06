"""Explicit offline composition root and registry lifecycle commands."""
import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from prompt_optimizer import __version__
from prompt_optimizer.adapters.fake_llm import FakeLLMClient
from prompt_optimizer.adapters.fake_optimizer import FakeOptimizer
from prompt_optimizer.adapters.filesystem_registry import FilesystemPromptRepository
from prompt_optimizer.adapters.json_report import JsonReportWriter
from prompt_optimizer.adapters.jsonl_dataset import JsonlDatasetLoader
from prompt_optimizer.application import (EvaluatePrompt, OptimizePrompt, SaveCandidate,
    ApprovePrompt, RejectPrompt, PromotePrompt, load_dataset, load_train_validation)
from prompt_optimizer.application.reporting import json_value, terminal_summary, write_report
from prompt_optimizer.domain import OptimizationTask, PromptVersion
from prompt_optimizer.domain.evaluation import DeterministicEvaluator
from prompt_optimizer.domain.registry import RegistryError
from prompt_optimizer.ports import ReportWriter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompt-opt", description="Prompt Optimization Engine")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    for name in ("bootstrap", "evaluate", "optimize", "versions", "approve", "reject", "promote"):
        p = commands.add_parser(name)
        p.add_argument("--registry-root", required=True)
        p.add_argument("--prompt-name", required=True)
        if name != "versions":
            p.add_argument("--prompt-version", required=True)
        if name == "bootstrap":
            p.add_argument("--prompt-file", required=True)
        if name in ("evaluate", "optimize"):
            p.add_argument("--dataset-id", required=True)
            p.add_argument("--dataset-version", required=True)
            p.add_argument("--client", choices=["fake"], required=True)
            p.add_argument("--responses", required=True)
            p.add_argument("--report", required=True)
        if name == "evaluate":
            p.add_argument("--dataset", required=True)
        if name == "optimize":
            p.add_argument("--train", required=True)
            p.add_argument("--validation", required=True)
            p.add_argument("--task-id", required=True)
            p.add_argument("--optimizer", choices=["fake"], required=True)
            p.add_argument("--candidate-file", required=True)
            p.add_argument("--candidate-version")
    return parser


def _responses(path: str) -> FakeLLMClient:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate response key")
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError("Nonfinite response")
    data = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=invalid_constant)
    if not isinstance(data, dict) or any(not isinstance(cases, dict) for cases in data.values()):
        raise ValueError("Responses must map versions to case maps")
    return FakeLLMClient({}, version_responses={(version, case): output
        for version, cases in data.items() for case, output in cases.items()})


def main(argv: Sequence[str] | None = None, *, report_writer: ReportWriter | None = None) -> int:
    """0 success, 1 technical failure, 3 scored failure/reject, 4 review; argparse exits 2."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    saved = None
    stage = "storage"
    try:
        repository = FilesystemPromptRepository(args.registry_root)
        if args.command == "bootstrap":
            prompt = repository.bootstrap(PromptVersion(args.prompt_name, args.prompt_version,
                Path(args.prompt_file).read_text(encoding="utf-8"), status="production"))
            print(f"{prompt.name}/{prompt.version}: {prompt.status.value}")
            return 0
        if args.command == "versions":
            for prompt in repository.list(args.prompt_name):
                print(json.dumps({"version": prompt.version, "status": prompt.status.value,
                    "parent_version": prompt.parent_version,
                    "provenance": json_value(repository.provenance(prompt.name, prompt.version))},
                    ensure_ascii=False))
            return 0
        if args.command in ("approve", "reject", "promote"):
            use_case = {"approve": ApprovePrompt, "reject": RejectPrompt, "promote": PromotePrompt}[args.command]
            prompt = use_case(repository).execute(args.prompt_name, args.prompt_version)
            print(f"{prompt.name}/{prompt.version}: {prompt.status.value}")
            return 0
        source = repository.read(args.prompt_name, args.prompt_version)
        stage = "input/execution"
        evaluator = EvaluatePrompt(_responses(args.responses), [DeterministicEvaluator()])
        loader = JsonlDatasetLoader()
        started = datetime.now(timezone.utc)
        if args.command == "evaluate":
            dataset = load_dataset(args.dataset, dataset_id=args.dataset_id,
                                   version=args.dataset_version, loader=loader)
            result = evaluator.execute(source, dataset)
            code = 3 if result.failed_case_ids else 0
        else:
            train, validation = load_train_validation(args.train, args.validation,
                dataset_id=args.dataset_id, version=args.dataset_version, loader=loader)
            candidate_version = args.candidate_version or repository.next_version(args.prompt_name)
            task = OptimizationTask(args.task_id, source, train.cases, validation.cases,
                                    args.dataset_id, args.dataset_version)
            result = OptimizePrompt(FakeOptimizer(Path(args.candidate_file).read_text(encoding="utf-8")),
                                    evaluator).execute(task, candidate_version=candidate_version)
            stage = "candidate storage"
            saved = SaveCandidate(repository).execute(result)
            code = {"approve": 0, "reject": 3, "review": 4}[result.recommendation.value]
        completed = datetime.now(timezone.utc)
        stage = "report"
        write_report(result, args.report, writer=report_writer if report_writer is not None else JsonReportWriter(),
                     started_at=started, completed_at=completed)
        print(terminal_summary(result))
        if saved is not None:
            print(f"Saved {saved.name}/{saved.version}: candidate; approval and promotion require explicit commands.")
        return code
    except Exception as exc:
        print(f"Failed during {stage}; no successful report/workflow completion.", file=sys.stderr)
        if isinstance(exc, RegistryError):
            print(f"Registry error: {type(exc).__name__}. Inspect registry state before retrying.", file=sys.stderr)
        if saved is not None:
            print(f"Saved candidate {saved.name}/{saved.version} remains candidate. Do not rerun optimization automatically.",
                  file=sys.stderr)
        return 1
