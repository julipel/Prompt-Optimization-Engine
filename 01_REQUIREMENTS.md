# 01. Требования

## Problem

Ручная оптимизация System Prompt плохо воспроизводима: сложно доказать
улучшение, обнаружить регрессии и понять, почему новая версия лучше.
Нужен движок, который сравнивает версии на фиксированном evaluation
dataset и может генерировать candidate через GEPA.

## Functional requirements

### FR-1 Prompt input

Система принимает имя prompt, версию и текст.

### FR-2 Dataset

Поддерживается JSONL dataset. Каждый case имеет `id`, `input`,
опциональный `context`, `expected`, опциональные tags.

### FR-3 Baseline evaluation

Система выполняет prompt на dataset и сохраняет результат каждого case и
агрегированные метрики.

### FR-4 Metrics

MVP: - task_success - groundedness - tool_selection -
instruction_following

Дополнительно critical checks: - price_hallucination -
fake_booking_confirmation - unsafe_action

Архитектура метрик должна позволять добавлять новые evaluator'ы.

### FR-5 GEPA

GEPA реализуется как adapter порта `PromptOptimizer`.

### FR-6 Candidate evaluation

После optimization новый prompt оценивается на validation dataset.

### FR-7 Regression gate

Candidate отклоняется, если: - появился любой запрещённый critical
failure; - нарушены настроенные regression thresholds.

### FR-8 Reports

Формируется machine-readable JSON report и человекочитаемое CLI summary:
before, after, delta, failures, critical failures, recommendation.

### FR-9 Registry

Prompt версии неизменяемы. Статусы: candidate, approved, rejected,
production, archived. Хранить parent version, optimizer, dataset
id/version, scores, timestamps.

### FR-10 Promotion

Promotion --- отдельная явная команда. Optimize никогда не делает
promotion автоматически.

## Non-functional

-   воспроизводимость;
-   testability без реального LLM;
-   provider-independent core;
-   понятные ошибки dataset/config;
-   секреты только через env;
-   отсутствие PHI/PII в демо dataset.

## Demo domain

Для примеров использовать синтетического AI-консультанта клиники:
услуги, цены, запись, tool selection, неизвестные вопросы и escalation.
Никаких реальных данных пациентов.
