"""Reproducible Phase 2 tool lifecycle; all data and outputs are explicit, no network."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.testclient import TestClient
from prompt_optimizer.adapters.agent_datasets import FileDatasetResolver
from prompt_optimizer.adapters.agent_http import HttpBackendConfiguration, HttpOptimizationBackend
from prompt_optimizer.adapters.filesystem_registry import FilesystemPromptRepository
from prompt_optimizer.adapters.jsonl_dataset import JsonlDatasetLoader
from prompt_optimizer.domain import PromptVersion
from prompt_optimizer.http_runtime import OfflineConfiguration, build_offline_app
from prompt_optimizer.interfaces.agent_tool import AgentTools, tool_definitions


def main() -> None:
    with TemporaryDirectory(prefix="agent-tool-demo-") as directory:
        root = Path(directory)
        repository = FilesystemPromptRepository(root / "registry")
        repository.bootstrap(PromptVersion("clinic", "v001", "Передавай оператору", status="production"))
        train = {"id": "train", "input": "Неизвестная услуга?", "expected": {"action": "escalate"}}
        validation = {"id": "unknown", "input": "Неизвестная цена?",
                      "expected": {"action": "escalate", "must_not_invent": True}}
        for split, case in (("train", train), ("validation", validation)):
            (root / f"{split}.jsonl").write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
        app = build_offline_app(OfflineConfiguration(str(root / "registry"),
            "Не выдумывай цены. Передавай неизвестные вопросы оператору.", {
                ("v001", "unknown"): {"action": "answer"},
                ("v002", "unknown"): {"action": "escalate", "note": "Проверено"},
            }))
        with TestClient(app) as http:
            backend = HttpOptimizationBackend(HttpBackendConfiguration("http://testserver"), client=http)
            resolver = FileDatasetResolver({("demo", "1"): (
                str(root / "train.jsonl"), str(root / "validation.jsonl"))}, JsonlDatasetLoader())
            tools = AgentTools(backend, resolver)
            # Host exposes these two definitions/callables to its preferred SDK.
            assert {d["name"] for d in tool_definitions()} == {"optimize_prompt", "get_optimization_report"}
            summary = tools.optimize_prompt({"prompt_name": "clinic", "production": True,
                "dataset_id": "demo", "dataset_version": "1", "candidate_version": "v002"})
            full = tools.get_optimization_report({"report_id": summary["report"]["id"]})
            assert summary["recommendation"] == "approve"
            assert full["report"]["schema_version"] == "1.0"
            assert repository.read("clinic", "v002").status.value == "candidate"
            assert repository.production("clinic").version == "v001"
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            # Explicit separate human actions: never register these as agent tools.
            assert http.post("/prompts/clinic/v002/approve").json()["status"] == "approved"
            assert http.post("/prompts/clinic/v002/promote").json()["status"] == "production"
            assert repository.production("clinic").version == "v002"
            assert tools.get_optimization_report({"report_id": full["id"]}) == full
            print("Offline lifecycle verified; temporary registry and report IDs expire on exit.")


if __name__ == "__main__":
    main()
