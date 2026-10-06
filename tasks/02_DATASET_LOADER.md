# Session 02 --- Dataset loader

## Goal

Надёжно читать и валидировать JSONL evaluation datasets.

## Implement

-   Dataset port/model;
-   JSONL adapter;
-   validation errors with line/case id;
-   duplicate id detection;
-   train/validation loading;
-   demo clinic datasets.

## Tests

valid file, malformed JSON, missing required fields, duplicate ids,
empty dataset, optional context/tags.

## Acceptance

Dataset можно загрузить одной application-facing функцией; domain не
знает о filesystem.
