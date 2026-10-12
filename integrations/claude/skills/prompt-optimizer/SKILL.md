---
name: prompt-optimizer
description: Optimize a system prompt whose model output is structured JSON (classification, field extraction, routing, tool choice) with the Prompt Optimization Engine via its MCP tools optimize_prompt and get_optimization_report. Use when the user asks to improve, tune or evaluate such a prompt against a labelled dataset, or to prepare a dataset for that. Not for free-text outputs.
---

# Prompt optimizer

The `prompt-optimizer` MCP tools call a separate optimization service. They only
create a `candidate` version and a recommendation.

## Before calling `optimize_prompt`

- Ask the user to confirm every call: with the real service it is billed and runs
  for minutes. Never retry on your own; after a timeout or transport error run
  `prompt-opt versions` first — a candidate may already exist.
- The prompt must already be in the registry and the dataset must be listed in the
  user's MCP config (`~/.config/prompt-optimizer/mcp.json`, key `"<id>@<version>"`).
  Adding a dataset there requires restarting the MCP server (a new Claude session).

## Preparing a dataset

JSONL, one case per line:

```json
{"id": "ticket_017", "input": "Списали деньги дважды", "context": {"plan": "pro"}, "expected": {"category": "billing", "priority": "high"}, "tags": ["billing"]}
```

- Every `expected` key is compared exactly. Fix the vocabulary and **write the allowed
  values into the prompt itself** — the optimizer will not reliably guess label
  spellings (in the clinic case study it chose `date` where the data said `дата`).
- Optional demo-domain checks: `price` + `must_not_invent` (price must come from
  `context.knowledge`), `must_not_claim_booking`, `must_ask`, `tool`.
- Files: `train.jsonl`, `validation.jsonl` (drives candidate selection) and a separate
  `test.jsonl` for the final check; no shared ids or inputs; 20–40 cases each to start.
- Check before registering: `prompt-opt validate-dataset train.jsonl validation.jsonl test.jsonl`
  — fix every ERROR, and scan the printed value counts for label typos.
- Avoid putting one fixed lookup table in every case: the optimizer may copy it into
  the prompt and the test split will not notice.

## After the run

Report the candidate, recommendation, before/after metrics, failed gates, critical
failures and still-failing cases (details via `get_optimization_report`), plus
anything odd in the candidate text (length growth, copied data, `optimizer: fake`
meaning the offline demo service). Never approve or promote — the human does:

```bash
prompt-opt versions --registry-root <root> --prompt-name <name>
prompt-opt approve  --registry-root <root> --prompt-name <name> --prompt-version <candidate>
prompt-opt promote  --registry-root <root> --prompt-name <name> --prompt-version <candidate>
```

If `prompt-opt` is not on `PATH`, it is in the engine's virtualenv:
`<engine-repo>/.venv/bin/prompt-opt`.
