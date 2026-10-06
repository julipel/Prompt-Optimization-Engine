# Session 06 --- Prompt Registry

## Goal

Версионировать prompts в filesystem.

## Layout example

prompts/`<name>`{=html}/v001.md prompts/`<name>`{=html}/v001.json

## Implement

-   PromptRepository port;
-   filesystem adapter;
-   next version generation;
-   metadata;
-   immutable stored versions;
-   statuses;
-   explicit approve/reject/promote operations;
-   only one production version per prompt.

## Safety

Optimize creates `candidate`. Никакого auto-promote.

## Tests

create/read/list, duplicate version, transitions, production uniqueness,
parent relation.

## Acceptance

Можно восстановить историю изменений и происхождение candidate.
