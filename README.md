# Prompt Optimization Engine

Provider-independent Python-движок для оценки, автоматической оптимизации
(DSPy + GEPA) и версионирования системных LLM-промптов. Доменная логика не
зависит от конкретного LLM-провайдера или оптимизатора; все внешние
зависимости живут только в adapter-слое.

Демо-домен: синтетический AI-консультант клиники (цены, запись, escalation).
Без данных реальных пациентов.

## Главный принцип

GEPA (или любой другой optimizer) не имеет права автоматически менять
production prompt. Результат оптимизации всегда создаётся как `candidate`
и проходит evaluation, regression gates и явное human approval + promotion.

## Статус

- Python 3.11+, протестировано на 3.14.
- Полный offline test suite: **516 passed, 2 skipped** (пропущен только
  `gepa_smoke`, требующий реальных credentials).
- Реальный provider boundary (DSPy 3.4.0 / GEPA 0.1.4) проверен с OpenAI
  (`gpt-5.6-terra` / `gpt-5.6-sol`), см. [Реальный провайдер (OpenAI)](#реальный-провайдер-openai).

```
python -m pytest -q -p no:cacheprovider
```

## Архитектура

Гексагональная слоистая архитектура; зависимости идут только внутрь
(`interfaces -> application -> domain`), adapters реализуют ports:

| Слой | Назначение | Ключевые модули |
| --- | --- | --- |
| `domain` | Неизменяемые модели и чистые правила, без I/O | `models.py`, `evaluation.py`, `comparison.py`, `registry.py`, `dataset.py`, `failures.py` |
| `application` | Use cases, оркестрация доменной логики | `evaluation.py`, `optimization.py`, `comparison.py`, `registry.py`, `reporting.py`, `failures.py` |
| `ports` | Protocols для внешних зависимостей | `LLMClient`, `PromptOptimizer`, `Evaluator`, `PromptRepository`, `ReportWriter`, `DatasetResolver`, agent `OptimizationBackend` |
| `adapters` | Конкретные реализации ports | DSPy/GEPA optimizer, filesystem registry, JSONL loader, JSON report writer, HTTP agent backend, fake LLM/optimizer для офлайн-тестов |
| `interfaces` | Внешние точки входа | CLI (`prompt-opt`), FastAPI (`http.py`), agent tool (`agent_tool.py`) |

`domain`/`application` никогда не импортируют DSPy/GEPA или HTTP-библиотеки —
эти зависимости изолированы в adapters/interfaces.

Полная спецификация и история реализации по сессиям — в [`tasks/`](tasks/)
и [`01_REQUIREMENTS.md`](01_REQUIREMENTS.md) / [`02_ARCHITECTURE.md`](02_ARCHITECTURE.md) /
[`03_DATA_CONTRACTS.md`](03_DATA_CONTRACTS.md) / [`04_TEST_STRATEGY.md`](04_TEST_STRATEGY.md).
Правила разработки — в [`AGENTS.md`](AGENTS.md).

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate           # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest -q
python -m prompt_optimizer --help
prompt-opt --version
```

## Возможности

### Evaluation engine

`EvaluatePrompt(client, evaluators).execute(prompt, dataset)` прогоняет
prompt на JSONL dataset и возвращает `EvaluationResult` с per-case outputs
и агрегированными метриками.

Встроенные deterministic метрики (`DeterministicEvaluator`):
`task_success`, `groundedness`, `tool_selection`, `instruction_following`;
critical checks: `price_hallucination`, `fake_booking_confirmation`,
`unsafe_action`. Каждая метрика бинарна (`1`/`0` + details); critical
failure сохраняется в `result.critical_failures` независимо от aggregate
score. Архитектура позволяет добавлять custom evaluators, включая
LLM-as-a-judge для семантики, не выражаемой кодом — но по умолчанию
используется только deterministic подход (правило из `AGENTS.md`:
«если критерий можно проверить кодом, не использовать LLM-as-a-judge»).

### Optimization pipeline

`OptimizePrompt(optimizer, evaluation, thresholds=None).execute(task,
candidate_version=...)`: оценивает baseline на validation, запускает любой
`PromptOptimizer` (adapter port) на train+validation, оценивает candidate
на том же dataset, сравнивает через `CompareVersions` и возвращает
`recommendation` (`approve` / `reject` / `review`).

`RegressionThresholds` задаёт допустимые снижения метрик и hard minimum
scores. Любой critical failure candidate или нарушение threshold даёт
`reject`; строгое улучшение aggregate без regressions — `approve`.
`approve` — это только рекомендация движка, не deployment.

`GEPAOptimizer` (DSPy 3.4.0 / GEPA 0.1.4) и `FakeOptimizer` взаимозаменяемы
через один и тот же порт `PromptOptimizer`. GEPA использует
`DeterministicFeedback`, который вызывает существующий `EvaluatePrompt` на
каждый metric call — без отдельного LLM judge при reflection.

### Prompt Registry

`FilesystemPromptRepository(root)` хранит все версии одного prompt name в
одном атомарном JSON-снапшоте (`<root>/prompts/<name>.json`), с interprocess
lock и atomic replace. Lifecycle:

| Операция | Из статуса | В статус |
| --- | --- | --- |
| `create` | — | `candidate` |
| `bootstrap` | пустая история | `production` |
| `approve` | `candidate` | `approved` |
| `reject` | `candidate` | `rejected` |
| `promote` | `approved` | `production` (прежняя production → `archived`) |

Версии и текст неизменяемы после создания; меняется только статус.
Максимум одна `production` версия на prompt name.

### Reports

JSON report schema `1.0` (`prompt_optimizer.application.reporting`):
evaluation и optimization reports с полными outputs, per-case metrics,
before/after/deltas, gates и recommendation. Запись атомарна
(temp file + fsync + `os.replace`).

### CLI

Entry point `prompt-opt` / `python -m prompt_optimizer`. Все команды требуют
`--registry-root` и `--prompt-name`:

| Команда | Назначение |
| --- | --- |
| `bootstrap --prompt-version V --prompt-file FILE` | Регистрирует исходный production (только для пустой истории) |
| `evaluate --prompt-version V --dataset FILE --dataset-id ID --dataset-version VER --client fake --responses FILE --report FILE` | Подробная evaluation |
| `optimize --prompt-version V --train FILE --validation FILE --dataset-id ID --dataset-version VER --task-id ID --client fake --responses FILE --optimizer fake --candidate-file FILE --report FILE [--candidate-version V]` | Pipeline + SaveCandidate |
| `versions` | JSON-строки version/status/parent/provenance |
| `approve` / `reject` / `promote` | Отдельные явные lifecycle-команды |

Exit codes: `0` успех, `1` ошибка ввода/исполнения/registry/report,
`2` неверные CLI-аргументы, `3` evaluation с failed cases или optimization
reject, `4` optimization review. CLI сейчас поддерживает только явно
выбранные `--client fake` / `--optimizer fake`; реальный provider
настраивается через Python API (см. ниже), сознательно не добавлен в CLI
в этой итерации.

### HTTP API (FastAPI)

`prompt_optimizer.interfaces.http.create_app(HttpDependencies(...))` —
изолированное приложение поверх тех же application use cases:

| Endpoint | Ответ |
| --- | --- |
| `POST /evaluations` | 200, evaluation report 1.0 |
| `POST /optimizations` | 201, `{id, report}` |
| `GET /optimizations/{id}` | 200, сохранённый результат |
| `GET /prompts/{name}/versions` | 200, список версий |
| `GET /prompts/{name}/production` | 200 / 404 |
| `POST /prompts/{name}/{version}/approve\|reject\|promote` | 200, snapshot |

`prompt_optimizer.http_runtime.build_offline_app` — composition root для
offline-демо (filesystem registry + fake client/optimizer + in-memory result
store). Result store живёт только в памяти одного процесса; для MVP
рассчитан на один worker. Подробности ошибок и error codes —
в исходном HTTP-модуле и тестах `tests/test_http.py`.

### Agent tool

`prompt_optimizer.interfaces.agent_tool.AgentTools` — независимый от agent
SDK интерфейс с двумя callable: `optimize_prompt` и
`get_optimization_report`, поверх HTTP API через узкий port
`OptimizationBackend`. Tool не имеет approve/reject/promote/deploy
capabilities — только optimization и чтение report. Подробный контракт:
[`docs/AGENT_TOOL.md`](docs/AGENT_TOOL.md), воспроизводимый пример:
[`examples/agent_offline.py`](examples/agent_offline.py).

### Failure analyzer

Контролируемый цикл: production trace → injected sanitization →
`FailureCase` → deterministic analysis → pending draft → отдельный human
review → явный export нового Dataset release. Pending/rejected drafts
нельзя экспортировать; failed output никогда не используется как expected.
Подробности: [`docs/FAILURE_ANALYZER.md`](docs/FAILURE_ANALYZER.md),
пример: [`examples/failures_offline.py`](examples/failures_offline.py).

## Быстрый старт (offline, без сети и платных API)

```python
from prompt_optimizer.adapters.fake_llm import FakeLLMClient
from prompt_optimizer.adapters.fake_optimizer import FakeOptimizer
from prompt_optimizer.application import EvaluatePrompt, OptimizePrompt, CompareVersions
from prompt_optimizer.domain import (
    EvaluationCase, OptimizationTask, PromptVersion, RegressionThresholds,
)
from prompt_optimizer.domain.evaluation import DeterministicEvaluator

task = OptimizationTask(
    task_id="demo",
    source_prompt=PromptVersion("clinic", "v001", "Передавай неизвестные вопросы оператору.",
                                status="production"),
    train_cases=(EvaluationCase("train", "Неизвестная услуга?", {"action": "escalate"}),),
    validation_cases=(EvaluationCase("validation", "Неизвестная цена?",
                                    {"action": "escalate", "must_not_invent": True}),),
    dataset_id="clinic-demo", dataset_version="1",
)
client = FakeLLMClient({}, version_responses={
    ("v001", "validation"): {"action": "answer"},
    ("v002", "validation"): {"action": "escalate"},
})
thresholds = RegressionThresholds(
    allowed_decrease={"task_success": 0, "instruction_following": 0, "aggregate": 0},
    minimum_scores={"task_success": 1},
)
result = OptimizePrompt(
    FakeOptimizer("Не выдумывай цены. Передавай неизвестные вопросы оператору."),
    EvaluatePrompt(client, [DeterministicEvaluator()]), thresholds,
).execute(task, candidate_version="v002")

assert result.recommendation.value == "approve"
assert result.candidate.status.value == "candidate"  # approve != promotion
```

Полные воспроизводимые сценарии (registry lifecycle, CLI, HTTP, agent tool,
failure analyzer) — в [`examples/`](examples/) и соответствующих разделах
`tasks/*.md`.

## Реальный провайдер (OpenAI)

`GEPAOptimizer` и `FakeOptimizer` взаимозаменяемы через один порт
`PromptOptimizer` — чтобы перейти на реальный LLM-вызов, конфигурируется
`GEPAConfig` (LiteLLM provider/model-нотация):

```python
from prompt_optimizer.adapters.dspy.gepa_optimizer import GEPAConfig, GEPAOptimizer

config = GEPAConfig(
    model="openai/gpt-5.6-terra",       # основной inference — баланс цена/качество
    reflection_model="openai/gpt-5.6-sol",  # reflection — reasoning, вызывается реже
    credential_env="OPENAI_API_KEY",    # имя env-переменной, не сам ключ
    max_tokens=16000,                   # OpenAI reasoning-тир требует >= 16000
)
optimizer = GEPAOptimizer(config)
# optimizer.optimize(task, candidate_version="v002") — реальный платный вызов
```

Готовая конфигурация: [`examples/gepa_openai_config.py`](examples/gepa_openai_config.py).
Секрет читается только из `os.environ[credential_env]` непосредственно перед
вызовом, нигде не сохраняется и не логируется; в registry/report metadata
ключи-кандидаты (`secret`, `password`, `credential`, `api_key`, ...)
отклоняются на уровне domain.

Для `openai/gpt-5.6-*` (reasoning-тир) обязательны `temperature=1.0`
(дефолт `GEPAConfig`) и `max_tokens >= 16000` — иначе DSPy отклоняет вызов
ещё до обращения к сети.

Опциональный integration-тест с реальным провайдером (`gepa_smoke`) не
входит в обычный `pytest`:

```bash
export OPENAI_API_KEY="..."          # только в окружении, никогда в файлах репозитория
export GEPA_MODEL="openai/gpt-5.6-terra"
export GEPA_REFLECTION_MODEL="openai/gpt-5.6-sol"
export GEPA_MAX_TOKENS="16000"
pytest -q -p no:cacheprovider -m gepa_smoke --run-gepa-smoke
```

## Структура проекта

```
src/prompt_optimizer/
├── domain/          # неизменяемые модели, чистые правила
├── application/      # use cases
├── ports/             # protocols для внешних зависимостей
├── adapters/          # DSPy/GEPA, filesystem registry, JSONL, JSON report, HTTP agent backend, fakes
├── interfaces/        # CLI, FastAPI, agent tool
├── cli.py
└── http_runtime.py    # composition root для offline HTTP-демо

datasets/clinic/        # синтетические demo JSONL datasets
examples/                # воспроизводимые offline-сценарии + GEPA/OpenAI config
docs/                     # agent tool и failure analyzer contracts
tasks/                    # спецификации сессий разработки (историческая трассировка)
tests/                    # unit/integration/regression suite
```
