"""MCP (stdio) bridge: publishes the agent tools to Claude Code, Claude Desktop or any MCP host.

A thin client of the HTTP service: no DSPy, no API keys, no lifecycle actions.
Tool schemas and error handling are exactly those of ``AgentTools``; the host
configuration names the service URL and maps dataset ids to local files, so the
agent never passes paths.

    prompt-opt-mcp --config ~/.config/prompt-optimizer/mcp.json
"""
import argparse
from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from prompt_optimizer.adapters.agent_datasets import FileDatasetResolver
from prompt_optimizer.adapters.agent_http import HttpBackendConfiguration, HttpOptimizationBackend
from prompt_optimizer.adapters.jsonl_dataset import JsonlDatasetLoader
from prompt_optimizer.interfaces.agent_tool import AgentTools, tool_definitions

INSTRUCTIONS = ("Prompt optimization service. optimize_prompt creates a candidate prompt version "
                "and a recommendation; it never approves, promotes or deploys. Report the result "
                "to the human, who approves and promotes explicitly.")


@dataclass(frozen=True)
class McpConfiguration:
    base_url: str
    datasets: Mapping[tuple[str, str], tuple[str, str]]
    timeout_seconds: float = 1800.0  # GEPA runs take minutes; a short timeout leaves outcome unknown.

    @classmethod
    def load(cls, path: str | Path) -> "McpConfiguration":
        """Read JSON; dataset paths are resolved relative to the config file."""
        path = Path(path).expanduser()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("datasets"), dict):
            raise ValueError("Config must be an object with base_url and datasets")
        datasets = {}
        for key, files in raw["datasets"].items():
            dataset_id, sep, version = key.rpartition("@")
            if not sep or not dataset_id or not version or not isinstance(files, dict):
                raise ValueError(f"Dataset key must be 'id@version' mapping to train/validation: {key!r}")
            datasets[dataset_id, version] = tuple(
                str((path.parent / Path(files[split]).expanduser()).resolve()) for split in ("train", "validation"))
        return cls(base_url=raw["base_url"], datasets=datasets,
                   timeout_seconds=float(raw.get("timeout_seconds", 1800)))


def build_tools(config: McpConfiguration, *, client: Any | None = None) -> AgentTools:
    backend = HttpOptimizationBackend(HttpBackendConfiguration(config.base_url, config.timeout_seconds),
                                      client=client)
    return AgentTools(backend, FileDatasetResolver(config.datasets, JsonlDatasetLoader()))


def list_tools() -> types.ListToolsResult:
    # Output/error schemas stay in AgentTools docs: MCP would validate errors against output_schema.
    return types.ListToolsResult(tools=[
        types.Tool(name=d["name"], description=d["description"], input_schema=d["input_schema"])
        for d in tool_definitions()])


async def call_tool(tools: AgentTools, name: str, arguments: Mapping[str, Any] | None) -> types.CallToolResult:
    # Optimization blocks for minutes; keep the MCP session responsive meanwhile.
    result = await anyio.to_thread.run_sync(tools.invoke, name, dict(arguments or {}))
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                                structured_content=result, is_error="error" in result)


def create_server(tools: AgentTools) -> Server:
    async def on_list_tools(context, params) -> types.ListToolsResult:
        return list_tools()

    async def on_call_tool(context, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await call_tool(tools, params.name, params.arguments)

    return Server("prompt-optimizer", version="0.1.0", instructions=INSTRUCTIONS,
                  on_list_tools=on_list_tools, on_call_tool=on_call_tool)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve prompt optimization tools over MCP stdio.")
    parser.add_argument("--config", required=True, help="JSON: base_url, timeout_seconds, datasets")
    args = parser.parse_args()
    server = create_server(build_tools(McpConfiguration.load(args.config)))

    async def run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(run)
