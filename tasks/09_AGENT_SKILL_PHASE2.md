# Session 09 --- Agent skill/tool (Phase 2)

## Goal

Сделать безопасный интерфейс, через который внешний агент может
запросить оптимизацию.

## Tool

`optimize_prompt`

Input: - prompt_name - source_version (или production) - dataset
id/version

Output: - candidate version - before/after metrics - regressions -
critical failures - recommendation - report id/path

## Safety

Tool НЕ имеет capability автоматического promote/deploy. Даже
recommendation=approve означает ожидание human approval.

## Tests

contract tests, API failure handling, no-promotion invariant.

## Acceptance

Skill можно подключить к агенту как независимый tool без знания
DSPy/GEPA.
