# Prompt Optimization Engine

Python-движок, который оценивает, автоматически улучшает (DSPy + GEPA) и
версионирует системные LLM-промпты со структурным JSON-ответом. Ядро не
зависит от LLM-провайдера и оптимизатора: DSPy, GEPA, HTTP и MCP живут только
в adapter- и interface-слоях.

- **Кейс:** [GEPA улучшает промпт AI-консультанта клиники на реальной модели](docs/CASE_STUDY.md).
  55 синтетических кейсов, на отложенном test aggregate **0.396 → 0.955**,
  результат бесплатно воспроизводится офлайн.
- **Подключение к агенту:** [MCP-сервер и skill для Claude Code / Claude Desktop](docs/INTEGRATION.md).
  Оптимизатор работает отдельным сервисом, агент может только запросить
  оптимизацию и прочитать отчёт.

## Главный принцип

Оптимизатор никогда не меняет production-промпт сам. Его результат всегда
сохраняется как `candidate`, проходит оценку и regression gates, а в
production попадает только после явных `approve` и `promote` человеком.
Critical failure блокирует одобрение независимо от среднего балла.

## Статус

- Python 3.11+, протестировано на 3.12 и 3.14.
- Тесты работают без сети и платных API: `pytest -q`. Два теста пропускаются
  намеренно: `gepa_smoke` делает реальные запросы к OpenAI и запускается только
  явно, а проверка junction-ссылок в реестре выполняется только в Windows.
- Реальный провайдер (DSPy 3.4.0 / GEPA 0.1.4) проверен с OpenAI
  `gpt-5.6-terra` / `gpt-5.6-sol`: smoke-тест и записанный прогон кейса.
- Связка Claude Code → MCP → сервис → реестр проверена в живой сессии на
  офлайн-демо сервисе.

## Как это устроено

```
           CLI (prompt-opt)      FastAPI-сервис       MCP-сервер (prompt-opt-mcp)
                  │                    │                ← Claude Code / Desktop
                  └──────────┬─────────┘
                       application: EvaluatePrompt, OptimizePrompt, CompareVersions, реестр
                             │
                       domain: модели, детерминированные метрики, regression gates
                             │
   adapters: GEPA и DSPyLLMClient (OpenAI и др.) · fake LLM/optimizer · файловый реестр · JSONL · отчёты
```

Зависимости направлены только внутрь (`interfaces → application → domain`),
adapters реализуют ports:

| Слой | Назначение | Ключевые модули |
| --- | --- | --- |
| `domain` | Неизменяемые модели и чистые правила, без I/O | `models.py`, `evaluation.py`, `comparison.py`, `registry.py`, `dataset.py`, `failures.py` |
| `application` | Use cases | `evaluation.py`, `optimization.py`, `comparison.py`, `registry.py`, `reporting.py`, `failures.py` |
| `ports` | Protocols внешних зависимостей | `LLMClient`, `PromptOptimizer`, `Evaluator`, `PromptRepository`, `ReportWriter`, `DatasetResolver`, `OptimizationResultStore`, `OptimizationBackend` |
| `adapters` | Реализации ports | `GEPAOptimizer`, `DSPyLLMClient`, `RecordingLLMClient`, fake LLM/optimizer, файловый реестр, JSONL loader, JSON-отчёты, хранилища результатов (память / файлы), HTTP-backend агента |
| `interfaces` | Точки входа | CLI, FastAPI (`http.py`), agent tool (`agent_tool.py`), MCP-сервер (`mcp_server.py`) |

Спецификация и история разработки: [`tasks/`](tasks/),
[`01_REQUIREMENTS.md`](01_REQUIREMENTS.md), [`02_ARCHITECTURE.md`](02_ARCHITECTURE.md),
[`03_DATA_CONTRACTS.md`](03_DATA_CONTRACTS.md), [`04_TEST_STRATEGY.md`](04_TEST_STRATEGY.md).
Правила разработки: [`AGENTS.md`](AGENTS.md).

## Установка

Зависимости объявлены в [`pyproject.toml`](pyproject.toml): версии `dspy` и
`gepa` закреплены, дополнительные пакеты подключаются через extras.

```bash
python3 -m venv .venv
source .venv/bin/activate           # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"             # dev: pytest, httpx2, mcp; только MCP: ".[mcp]"
pytest -q
prompt-opt --version
```

## Быстрый старт: CLI офлайн

Без сети и платных API. Ответы модели и текст кандидата заданы явно, поэтому
результат воспроизводим. Команды выполняются из каталога с `pyproject.toml`:

```bash
mkdir -p demo && cd demo
printf 'Передавай неизвестные вопросы оператору' > source.txt
printf 'Не выдумывай цены. Передавай неизвестные вопросы оператору.' > candidate.txt
cat > train.jsonl <<'JSON'
{"id":"unknown","input":"Неизвестная цена?","expected":{"action":"escalate","must_not_invent":true}}
JSON
cp train.jsonl validation.jsonl
cat > responses.json <<'JSON'
{
  "v001": {"unknown": {"action": "answer"}},
  "v002": {"unknown": {"action": "escalate"}}
}
JSON

ID=(--registry-root demo-registry --prompt-name clinic)
DS=(--dataset-id demo --dataset-version 1 --client fake --responses responses.json)

prompt-opt bootstrap "${ID[@]}" --prompt-version v001 --prompt-file source.txt
prompt-opt evaluate "${ID[@]}" --prompt-version v001 "${DS[@]}" --dataset validation.jsonl --report evaluation.json
# exit=3: baseline проваливает "unknown" (отчёт всё равно записан)
prompt-opt optimize "${ID[@]}" --prompt-version v001 "${DS[@]}" \
  --train train.jsonl --validation validation.jsonl --task-id demo \
  --optimizer fake --candidate-file candidate.txt --candidate-version v002 --report optimization.json
# recommendation: approve → кандидат сохранён, но ещё не production
prompt-opt versions "${ID[@]}"
prompt-opt approve "${ID[@]}" --prompt-version v002
prompt-opt promote "${ID[@]}" --prompt-version v002
prompt-opt versions "${ID[@]}"   # v002 — production, v001 — archived
```

Тот же механизм воспроизводит записанный реальный прогон из кейса: команды
есть в [`docs/CASE_STUDY.md`](docs/CASE_STUDY.md#воспроизвести-бесплатно).

## HTTP-сервис

Есть две сборки сервиса, обе — uvicorn factory в `prompt_optimizer.http_runtime`:

| Factory | Модель и оптимизатор | Отчёты |
| --- | --- | --- |
| `create_demo_app` | Fake-ответы для примера ниже, без сети | В памяти процесса |
| `create_real_app` | GEPA + `DSPyLLMClient` на одной DSPy-сигнатуре, платно | Файлы, сохраняются после перезапуска |

Офлайн-демо:

```bash
printf 'Передавай неизвестные вопросы оператору.' > http-source.txt
prompt-opt bootstrap --registry-root http-demo-registry --prompt-name clinic \
  --prompt-version v001 --prompt-file http-source.txt
PROMPT_REGISTRY_ROOT="$(pwd)/http-demo-registry" \
  uvicorn --factory prompt_optimizer.http_runtime:create_demo_app --host 127.0.0.1 --port 8000
```

```bash
curl http://127.0.0.1:8000/prompts/clinic/versions
curl -X POST http://127.0.0.1:8000/evaluations -H 'Content-Type: application/json' -d '{
  "prompt_name": "clinic", "prompt_version": "v001",
  "dataset": {"id": "demo", "version": "1", "cases": [
    {"id": "unknown", "input": "Неизвестная цена?",
     "expected": {"action": "escalate", "must_not_invent": true}}
  ]}
}'
```

Реальный сервис настраивается переменными окружения (`PROMPT_REGISTRY_ROOT`,
`GEPA_MODEL`, `GEPA_REFLECTION_MODEL`, ключ API и другие) — полный список в
[`docs/INTEGRATION.md`](docs/INTEGRATION.md#2-запуск-сервиса). Аутентификации у
сервиса нет, поэтому запускайте его только на `127.0.0.1`.

| Endpoint | Ответ |
| --- | --- |
| `POST /evaluations` | 200, evaluation report 1.0 |
| `POST /optimizations` | 201, `{id, report}` |
| `GET /optimizations/{id}` | 200, сохранённый результат |
| `GET /prompts/{name}/versions` | 200, список версий |
| `GET /prompts/{name}/production` | 200 / 404 |
| `POST /prompts/{name}/{version}/approve\|reject\|promote` | 200, snapshot |

OpenAPI: `GET /openapi.json`, интерактивная документация: `/docs`. Коды ошибок
описаны в `interfaces/http.py` и `tests/test_http.py`.

## Подключение к агенту

`prompt-opt-mcp` публикует для Claude Code, Claude Desktop и любого
MCP-совместимого агента два инструмента: `optimize_prompt` и
`get_optimization_report`. Это тонкий клиент сервиса: без ключей API, без
DSPy и без операций approve/promote. Датасеты агент указывает по id, а пути к
файлам задаются в конфиге пользователя. Skill в
[`integrations/claude/skills/prompt-optimizer/`](integrations/claude/skills/prompt-optimizer/SKILL.md)
учит Claude готовить датасеты, спрашивать подтверждение перед платным
запуском и отдавать решение человеку.

```bash
claude mcp add --scope user prompt-optimizer -- \
  ~/Prompt-Optimization-Engine/.venv/bin/prompt-opt-mcp --config ~/.config/prompt-optimizer/mcp.json
```

Полная инструкция, включая Claude Desktop под Windows + WSL, —
[`docs/INTEGRATION.md`](docs/INTEGRATION.md). Контракт инструментов для
своих агентов на любом SDK — [`docs/AGENT_TOOL.md`](docs/AGENT_TOOL.md).

## Возможности

### Оценка

`EvaluatePrompt(client, evaluators).execute(prompt, dataset)` прогоняет промпт
по датасету и возвращает ответы по каждому кейсу и агрегированные метрики.
Клиент — любой `LLMClient`:
- `DSPyLLMClient` — реальная модель;
- `FakeLLMClient` — заранее заданные ответы;
- `RecordingLLMClient` — обёртка, которая записывает ответы в формат
  `--responses`, чтобы платный прогон потом переигрывался офлайн.

Метрики `DeterministicEvaluator` проверяются кодом, без LLM-as-a-judge:

| Метрика | Проверяет | Critical |
| --- | --- | --- |
| `task_success` | Любые поля из `expected` совпадают точно | |
| `tool_selection` | Выбран ожидаемый `tool` | |
| `instruction_following` | Заданы обязательные уточнения (`must_ask`), правила не нарушены | |
| `groundedness` | Названная цена есть в `context.knowledge` | |
| `price_hallucination` | Цена не выдумана | ✔ |
| `fake_booking_confirmation` | Запись не подтверждена без результата инструмента | ✔ |
| `unsafe_action` | Нет флага небезопасного действия | ✔ |

Поэтому движок подходит для любых промптов с JSON-ответом: классификации,
извлечения полей, маршрутизации, выбора инструмента. Метрики демо-домена
клиники включаются только при наличии своих ключей в `expected`. Свободный
текст не поддерживается намеренно: правило проекта — «если критерий можно
проверить кодом, не использовать LLM-as-a-judge».

### Оптимизация

`OptimizePrompt(optimizer, evaluation, thresholds=None).execute(task, candidate_version=...)`
работает так:
1. оценивает baseline на validation;
2. запускает `PromptOptimizer` на train и validation;
3. оценивает кандидата на том же validation;
4. сравнивает версии и возвращает `recommendation`: `approve`, `review` или `reject`.

`RegressionThresholds` задаёт допустимое снижение метрик и минимальные
значения. Любой critical failure или нарушенный порог даёт `reject`, строгое
улучшение aggregate без регрессий — `approve`. `approve` — только
рекомендация, а не деплой.

`GEPAOptimizer` и `FakeOptimizer` взаимозаменяемы через один порт. GEPA
получает обратную связь от того же детерминированного оценщика: текстовый
разбор ошибок по каждому кейсу на каждый вызов метрики, без отдельного
LLM-судьи. `output_description` задаёт подсказку к выходному полю DSPy-сигнатуры.
По умолчанию она описывает поля клиники, реальный сервис использует
нейтральное описание JSON.

### Реестр промптов

`FilesystemPromptRepository(root)` хранит все версии одного промпта в одном
атомарном JSON-снапшоте (`<root>/prompts/<name>.json`) с межпроцессной
блокировкой.

| Операция | Из статуса | В статус |
| --- | --- | --- |
| `create` | — | `candidate` |
| `bootstrap` | пустая история | `production` |
| `approve` | `candidate` | `approved` |
| `reject` | `candidate` | `rejected` |
| `promote` | `approved` | `production` (прежняя production → `archived`) |

Текст версии после создания не меняется, меняется только статус. Production
у одного промпта не больше одной.

### Отчёты

JSON-отчёт schema `1.0` (`application/reporting.py`) содержит ответы и метрики
по каждому кейсу, значения до и после, разницу, gates и рекомендацию. Запись
атомарная. Сервис хранит результаты в `InMemoryOptimizationResultStore`
(демо) или в `FileOptimizationResultStore` (реальный сервис: один файл на
отчёт, только UUID-идентификаторы, без перезаписи).

### CLI

Точка входа — `prompt-opt` или `python -m prompt_optimizer`. Все команды,
кроме `validate-dataset`, требуют `--registry-root` и `--prompt-name`:

| Команда | Назначение |
| --- | --- |
| `validate-dataset FILE [FILE ...]` | Проверка JSONL до оптимизации: формат, повторы id/input между файлами, пустой `expected`, частоты значений каждого поля |
| `bootstrap --prompt-version V --prompt-file FILE` | Регистрирует исходный production (только при пустой истории) |
| `evaluate --prompt-version V --dataset FILE --dataset-id ID --dataset-version VER --client fake --responses FILE --report FILE` | Оценка на записанных ответах |
| `optimize --prompt-version V --train FILE --validation FILE --dataset-id ID --dataset-version VER --task-id ID --client fake --responses FILE --optimizer fake --candidate-file FILE --report FILE [--candidate-version V]` | Оптимизация на записанных ответах и сохранение кандидата |
| `versions` | JSON-строки version/status/parent/provenance |
| `approve` / `reject` / `promote` | Явные lifecycle-команды |

Коды выхода:
- `0` — успех;
- `1` — ошибка ввода, исполнения, реестра или отчёта, а также невалидный датасет;
- `2` — неверные аргументы CLI;
- `3` — в оценке есть проваленные кейсы или оптимизация дала `reject`;
- `4` — оптимизация дала `review`.

`evaluate` и `optimize` работают только с fake-клиентом и записанными ответами.
Реальная модель доступна через HTTP-сервис, MCP и Python API.

### Анализ сбоев

Контролируемый цикл превращает сбои из production в новые кейсы датасета:
1. трасса из production;
2. очистка от чувствительных данных;
3. `FailureCase`;
4. детерминированный анализ;
5. черновик кейса;
6. отдельное ревью человеком;
7. явный экспорт новой версии датасета.

Неодобренные черновики экспортировать нельзя. Ошибочный ответ модели никогда
не становится ожидаемым. Подробности — в [`docs/FAILURE_ANALYZER.md`](docs/FAILURE_ANALYZER.md),
пример — [`examples/failures_offline.py`](examples/failures_offline.py).

## Python API: офлайн-пример

```python
from prompt_optimizer.adapters.fake_llm import FakeLLMClient
from prompt_optimizer.adapters.fake_optimizer import FakeOptimizer
from prompt_optimizer.application import EvaluatePrompt, OptimizePrompt
from prompt_optimizer.domain import EvaluationCase, OptimizationTask, PromptVersion, RegressionThresholds
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

## Реальный провайдер (OpenAI)

Для перехода на реальную модель достаточно заменить fake-адаптеры. Модели
задаются в нотации LiteLLM `provider/model`:

```python
from prompt_optimizer.adapters.dspy.gepa_optimizer import GEPAConfig, GEPAOptimizer
from prompt_optimizer.adapters.dspy.llm_client import DSPyLLMClient

config = GEPAConfig(
    model="openai/gpt-5.6-terra",           # отвечает на кейсы — вызывается часто
    reflection_model="openai/gpt-5.6-sol",  # переписывает промпт — вызывается реже
    credential_env="OPENAI_API_KEY",        # имя переменной окружения, не сам ключ
    max_tokens=16000,                       # reasoning-модели OpenAI требуют >= 16000
)
optimizer = GEPAOptimizer(config)
client = DSPyLLMClient(config.model, config.credential_env, max_tokens=config.max_tokens)
```

Готовая конфигурация — [`examples/gepa_openai_config.py`](examples/gepa_openai_config.py).
Полный платный прогон с записью артефактов —
[`examples/clinic_case_study.py`](examples/clinic_case_study.py).

**Секреты.** Ключ читается из `os.environ[credential_env]` непосредственно
перед вызовом, нигде не сохраняется и не логируется. Текст ошибок провайдера
наружу не передаётся. Ключи с именами вроде `secret`, `password`, `api_key`
в metadata реестра и отчётов отклоняются на уровне domain.

**Ограничения моделей.** Для reasoning-моделей `openai/gpt-5.6-*` нужны
`temperature=1.0` (значение по умолчанию) и `max_tokens >= 16000`, иначе DSPy
отклонит вызов ещё до обращения к сети.

**Smoke-тест.** Интеграционный тест с реальным провайдером не входит в
обычный `pytest` и запускается явно:

```bash
export OPENAI_API_KEY="..."          # только в окружении, не в файлах репозитория
export GEPA_MODEL="openai/gpt-5.6-terra"
export GEPA_REFLECTION_MODEL="openai/gpt-5.6-sol"
export GEPA_MAX_TOKENS="16000"
pytest -q -m gepa_smoke --run-gepa-smoke
```

## Структура проекта

```
src/prompt_optimizer/
├── domain/            # неизменяемые модели, метрики, правила сравнения и реестра
├── application/       # use cases
├── ports/             # protocols внешних зависимостей
├── adapters/          # GEPA, DSPyLLMClient, recording/fake клиенты, реестр, JSONL, отчёты, хранилища
├── interfaces/        # FastAPI, agent tool, MCP-сервер
├── cli.py             # prompt-opt
└── http_runtime.py    # сборки сервиса: create_demo_app, create_real_app

datasets/clinic/          # минимальный датасет для unit-тестов
datasets/clinic_demo/     # 55 кейсов кейса клиники: train / validation / test
docs/                     # кейс, подключение к агенту, контракты agent tool и failure analyzer
docs/case_study/          # артефакты записанного реального прогона
examples/                 # офлайн-сценарии, конфигурация OpenAI, скрипт кейса
integrations/claude/      # skill и пример конфига MCP
tasks/                    # спецификации сессий разработки
tests/                    # unit, integration и regression тесты
```
