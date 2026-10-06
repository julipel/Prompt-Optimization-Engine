# Prompt Optimization Engine --- пакет для Codex

Цель проекта: создать независимый Python-сервис для оценки,
автоматической оптимизации и версионирования LLM-промптов. Первая
стратегия оптимизации --- DSPy + GEPA. В дальнейшем движок должен
поддерживать другие оптимизаторы без изменения application/domain слоя.

## Главный принцип

GEPA не имеет права автоматически менять production prompt. Результат
оптимизации всегда создаётся как candidate и проходит evaluation +
regression tests + human approval.

## MVP

-   загрузка prompt и evaluation dataset;
-   baseline evaluation;
-   DSPy/GEPA optimization;
-   повторная evaluation кандидата;
-   hard constraints для критических ошибок;
-   before/after report;
-   Prompt Registry с версиями и статусами;
-   CLI;
-   pytest unit/integration/regression tests.

## Phase 2

-   FastAPI;
-   agent skill/tool `optimize_prompt`;
-   сбор production failures;
-   `analyze_failures`;
-   другие optimizer adapters.

## Порядок работы Codex

Выполнять задачи из `tasks/` строго по порядку. Одна задача = одна
самостоятельная сессия. Перед изменениями прочитать `AGENTS.md`,
`01_REQUIREMENTS.md`, `02_ARCHITECTURE.md` и файл текущей задачи.

Не начинать следующую задачу, пока: 1. acceptance criteria текущей
задачи не выполнены; 2. тесты не проходят; 3. README/документация
обновлены, если публичный интерфейс изменился.

## Установка и запуск (Sessions 01–04)

Требуется Python 3.11+. Выполняйте команды из каталога с `pyproject.toml`.
В PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest -q
python -m prompt_optimizer --help
prompt-opt --version
```

Без активации окружения используйте `.\.venv\Scripts\python.exe` вместо
`python` и `.\.venv\Scripts\python.exe -m pytest -q` для тестов.

Реализован bootstrap: установка пакета, CLI с `--help`/`--version` и
валидируемые domain-модели и application evaluation engine. CLI-команды evaluation, optimization и registry
реализованы в Session 07. Runtime-зависимости Session 04:
DSPy 3.4.0 и GEPA 0.1.4, точные версии закреплены в `pyproject.toml`.

## Domain API

Публичные модели импортируются из `prompt_optimizer.domain`:

- `PromptVersion`: имя, версия, текст, статус, parent version, optimizer,
  dataset id/version, scores и timezone-aware `created_at`.
- `EvaluationCase`: `id`, `input`, `expected`, опциональные `context` и `tags`.
- `MetricResult`: имя, score в диапазоне `[0, 1]`, `passed`, `critical`, details.
- `CaseEvaluationResult`: structured output и metrics одного case.
- `EvaluationResult`: идентификаторы prompt/dataset, case results и scores;
  свойства `failed_case_ids` и `critical_failures` сохраняют детализацию ошибок.
- `OptimizationTask`: исходный prompt, train/validation cases, task/dataset ids.
- `OptimizationResult`: candidate, имя optimizer и JSON metadata.
- `PromptStatus` и `Recommendation`: статусы и рекомендации из требований.

Модели неизменяемы; вложенные JSON mapping/array копируются и замораживаются
в read-only mappings/tuples. Идентификаторы — непустые строки. Ошибки данных
вызывают `DomainValidationError`, наследник `DomainError` и `ValueError`.
Результат optimizer принимает только статус `candidate`. Переходы статусов
и promotion реализованы в application/registry (Session 06).

```python
from prompt_optimizer.domain import PromptVersion, EvaluationCase

prompt = PromptVersion(name="clinic", version="v001", text="Не выдумывай цены.")
case = EvaluationCase(
    id="unknown_price_001",
    input="Сколько стоит МРТ головы?",
    expected={"must_not_invent": True, "action": "escalate"},
)
```

Тесты Session 01 проверяют контракты, ошибки данных, неизменяемость,
сохранение critical failures и запуск CLI. Они не вызывают внешние API.

## Dataset API (Session 02)

Реализованы `Dataset` из `prompt_optimizer.domain`, порт `DatasetLoader`
из `prompt_optimizer.ports` и JSONL-адаптер `JsonlDatasetLoader`.
Application-функции принимают загрузчик явно; domain и application
не читают файловую систему. Dataset хранит непустые `id`, `version` и
неизменяемый упорядоченный tuple `cases`; идентификаторы задаёт вызывающий код.

```python
from prompt_optimizer.adapters.jsonl_dataset import JsonlDatasetLoader
from prompt_optimizer.application import load_dataset, load_train_validation

loader = JsonlDatasetLoader()
dataset = load_dataset(
    "datasets/clinic/train.jsonl", dataset_id="clinic-demo", version="1", loader=loader,
)
train, validation = load_train_validation(
    "datasets/clinic/train.jsonl", "datasets/clinic/validation.jsonl",
    dataset_id="clinic-demo", version="1", loader=loader,
)
# train.cases и validation.cases подходят для OptimizationTask.
```

Файлы читаются как UTF-8, по одному JSON-объекту на непустую строку.
Обязательны `id`, `input`, `expected`; `context` и `tags` можно опустить.
Неизвестные поля, повторные JSON-ключи, невалидные типы и нечисловые/бесконечные
JSON-константы отклоняются. Пустые строки пропускаются, порядок сохраняется.
Пустой dataset и повторяющиеся case ids внутри файла запрещены.

Ошибки загрузки вызывают `DatasetLoadError` (`ValueError`) с атрибутами
`source`, `line` (физический номер с 1, если доступен), `case_id` (если
доступен), `reason` и исходным exception в `__cause__`.
Результат возвращается только после проверки всего файла.

`load_train_validation` возвращает пару `(train, validation)` одного release,
не разделяет и не перемешивает данные автоматически. Проверка уникальности ids
выполняется отдельно для каждого split; пересечение ids между splits допустимо.
Демо-файлы в `datasets/clinic/` содержат разные синтетические cases для цен,
неизвестных услуг, записи/tool selection и escalation, без данных пациентов.

Тесты Session 02 проверяют JSONL, диагностику ошибок, уникальность,
пустые файлы, UTF-8, optional fields, модель, injected port и оба demo splits.
Evaluation engine (Session 03) использует этот Dataset API.

Проверка Sessions 01–02: 66 исходных тестов; Session 03 добавляет тесты evaluation.
Полный набор после Session 03: 122 теста проходят в `.venv`.
Для запуска без кэша pytest: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`.

## Evaluation API (Session 03)

Порты из `prompt_optimizer.ports` независимы от провайдера:

- `LLMClient.generate(prompt: PromptVersion, *, case_id: str, input: str,
  context: Mapping[str, Any]) -> Mapping[str, Any]` возвращает structured JSON.
  `expected` и tags не передаются клиенту, чтобы не раскрывать эталонный ответ.
- `Evaluator.evaluate(case: EvaluationCase, output: Mapping[str, Any])
  -> tuple[MetricResult, ...]` возвращает применимые метрики. Можно передать
  несколько evaluator'ов, но имена метрик внутри case должны быть уникальны.
- `EvaluatePrompt(client, evaluators).execute(prompt, dataset) -> EvaluationResult`
  из `prompt_optimizer.application` вызывает client один раз для каждого case,
  в порядке Dataset, затем evaluator'ы. Outputs копируются и замораживаются
  до проверки. Результат содержит prompt/dataset identity, каждый output,
  scores, passed/critical/details каждой метрики, failed case ids и critical failures.
- `DeterministicEvaluator` и `aggregate_metrics` находятся в
  `prompt_optimizer.domain.evaluation`: чистые правила без I/O и LLM judge.
- `FakeLLMClient(responses)` из `prompt_optimizer.adapters.fake_llm` воспроизводит
  явно заданные ответы по case id, копирует/замораживает их при создании и
  записывает вызовы в `calls`. Он не выводит ответы из `expected`.

Structured output использует `intent`, `price`, `action`, `tool`,
`asked` (массив названий запрошенных полей, например `["doctor"]`),
`booking_confirmed` (boolean) и `unsafe_action` (boolean). Отсутствующая
цена или `null` означают отсутствие ценового утверждения; отсутствующие
booking/unsafe flags означают `false`. Неверный тип safety flag проваливает
critical check. Провайдерский адаптер должен достоверно нормализовать ответ
в эту схему: свободный текст и скрытые в нём утверждения движок не анализирует.

Правила deterministic evaluator:

- `task_success`: точное структурное сравнение всех полей `expected`, кроме
  управляющих `must_not_invent`, `must_not_claim_booking`, `must_ask`.
  Отсутствующее поле проваливает проверку, в том числе при ожидаемом `null`.
  Порядок массивов значим; boolean не равен числу, но `3500 == 3500.0`.
- `tool_selection`: точное совпадение `tool`, только если он задан в `expected`.
- `groundedness` и critical `price_hallucination`: применяются при наличии
  ожидаемой цены или `must_not_invent=true`. Указанная output-цена должна
  совпадать с ожидаемой и, если `context.knowledge` задан, присутствовать среди
  его значений. Без ожидаемой цены любое ненулевое ценовое утверждение запрещено.
  Пропущенная известная цена проваливает task_success, но не является hallucination.
- `instruction_following`: применяется при наличии управляющих полей;
  проверяет включение всех `must_ask` в `asked`, запрет выдуманной цены
  при `must_not_invent=true` и применимые ограничения подтверждения записи.
- Critical `fake_booking_confirmation`: применяется к booking intent или
  `must_not_claim_booking`. При `must_not_claim_booking=true` подтверждение
  всегда запрещено. Иначе подтверждение требует
  `context.tool_result.success is True`. Tool result в output не считается
  доказательством; context предоставляет вызывающий код.
- Critical `unsafe_action`: применяется всегда; любое значение, кроме
  boolean `false` (или отсутствующего поля), проваливает проверку.

Каждая встроенная метрика бинарна: `1/passed` или `0/failed` с details.
Неприменимая метрика отсутствует, а не получает искусственный `1`.
`aggregate_metrics` считает арифметическое среднее каждой метрики только по
cases, где она присутствует. `scores["aggregate"]` — равновесное среднее
средних **некритических** метрик. Если есть только critical checks, aggregate
не создаётся. Custom evaluator может возвращать частичные scores `[0, 1]`.
Имя `aggregate` зарезервировано; critical flag одного имени должен совпадать
во всех cases. Пустая агрегация и case без метрик запрещены.

Critical checks имеют safety score (`1` означает отсутствие нарушения).
Их средние доступны в scores, но любой отдельный critical failure сохраняется
в `result.critical_failures` независимо от высокого aggregate. Aggregate сам
по себе не является разрешением одобрить candidate. Regression gate и
recommendation относятся к следующим задачам.

Ошибки генерации, невалидный JSON output, ошибки evaluator'а или его контракта
прерывают запуск с `EvaluationError`: `case_id`, `stage` (`generation`,
`evaluation`, `aggregation`) и исходная ошибка в `__cause__`. Частичный успешный
EvaluationResult не возвращается; автоматических retries нет. Отсутствующий
fake response вызывает `KeyError`, обёрнутый в EvaluationError.
Пустой список evaluator'ов вызывает `ValueError` при создании use case.
Некорректные управляющие поля expected вызывают `DomainValidationError`,
обёрнутый в EvaluationError. Обычные несовпадения ответов становятся метриками,
а не exceptions: остальные cases продолжают оцениваться.

Baseline на synthetic validation dataset без сети и платных API:

```python
from prompt_optimizer.adapters.fake_llm import FakeLLMClient
from prompt_optimizer.adapters.jsonl_dataset import JsonlDatasetLoader
from prompt_optimizer.application import EvaluatePrompt, load_dataset
from prompt_optimizer.domain import PromptVersion
from prompt_optimizer.domain.evaluation import DeterministicEvaluator

dataset = load_dataset(
    "datasets/clinic/validation.jsonl", dataset_id="clinic-demo", version="1",
    loader=JsonlDatasetLoader(),
)
client = FakeLLMClient({
    "validation_price_001": {"intent": "price", "price": 2000},
    "validation_unknown_price_001": {"intent": "price", "price": None, "action": "escalate"},
    "validation_booking_001": {
        "intent": "booking", "tool": "check_availability",
        "asked": ["doctor"], "booking_confirmed": False,
    },
    "validation_unknown_001": {"intent": "unknown", "action": "escalate"},
})
baseline = PromptVersion(name="clinic", version="v001", text="Не выдумывай цены.")
result = EvaluatePrompt(client, [DeterministicEvaluator()]).execute(baseline, dataset)
assert result.scores["aggregate"] == 1.0
assert len(result.cases) == 4
assert not result.critical_failures
print(dict(result.scores))
for case in result.cases:
    print(case.case_id, dict(case.output), case.metrics)
```

Этот пример проверяет wiring и evaluation на заданных outputs, а не качество
реальной модели. Тесты также проверяют оба demo splits, неверную/неизвестную
цену, tool selection, вопросы, forbidden booking, unsafe flags, агрегацию,
custom evaluator'ы, ошибки исполнения и сохранение critical failures.

## Optimization API (Session 04)

`PromptOptimizer` из `prompt_optimizer.ports` объявляет
`optimize(task: OptimizationTask, *, candidate_version: str) -> OptimizationResult`.
`optimizer.optimize(task, candidate_version=...)` возвращает исходный результат
адаптера. Domain/application/ports не импортируют DSPy, GEPA
или provider adapters. `FakeOptimizer` и `GEPAOptimizer` взаимозаменяемы без
изменения use case. Application pipeline, registry, reporting, evaluation
кандидата и promotion не входят в Session 04.

Версию candidate явно задаёт вызывающий код: непустая строка, отличная от
source version. Оба адаптера создают `PromptVersion` со статусом `candidate`,
исходным name, новым version/text, parent version, optimizer, dataset id/version
и UTC timestamp. Scores остаются пустыми: optimizer score не подменяет
отдельную evaluation кандидата. `OptimizationResult` сохраняет task id и JSON
metadata; GEPA добавляет версии библиотек, модели, seed, budget, ids обоих splits
и правило feedback. Ключи API и endpoint credentials в metadata не сохраняются.

### Проверенный DSPy/GEPA boundary

В `.venv` установлены **dspy==3.4.0**, **gepa==0.1.4**; проверка выполнена на
Python 3.14. Версия DSPy 3.0.4 ограничена Python `<3.14` и здесь не используется.
Сигнатуры и исходники установленного пакета проверены через `inspect`,
совместимость dependencies — `python -m pip check`. Официальные источники:
[DSPy GEPA API](https://dspy.ai/3.0.3/api/optimizers/GEPA/) и
[исходники релиза 3.4.0](https://github.com/stanfordnlp/dspy/blob/3.4.0/dspy/teleprompt/gepa/gepa.py).
Авторитетным для реализации является установленный релиз 3.4.0.

Используются реальные API: `dspy.LM`, `dspy.context(lm=..., adapter=JSONAdapter())`,
`Signature.with_instructions`, `Predict`, `Module`, `Example.with_inputs`,
`GEPA(metric=..., max_metric_calls=..., reflection_lm=..., num_threads=1, seed=...)`
и `GEPA.compile(student, *, trainset, valset) -> Module`.
Оптимизированный текст извлекается из `compiled.respond.signature.instructions`.
Модель и adapter настраиваются в локальном context, без изменения глобального LM.
LM cache выключен, retries отключены. Seed воспроизводит поиск, но не гарантирует
детерминизм внешнего провайдера. Budget ограничивает metric calls, а не денежные
расходы или число reflection calls.

Train и validation сохраняют порядок и передаются отдельно. DSPy Example
хранит case id, expected и tags для feedback; inputs — только input и context.
Эталонные expected/tags/id не попадают в inference prompt. Reflection получает
outputs и deterministic feedback, включая детали expected comparisons.
Вложенный domain JSON преобразуется в независимые dict/list.
Structured output объявлен как `dict[str, Any]` с полями схемы evaluation API;
свободный текст не трактуется как достоверный structured response.

`DeterministicFeedback` имеет пять аргументов
`(gold, pred, trace=None, pred_name=None, pred_trace=None)` и возвращает
`dspy.Prediction(score=..., feedback=...)`, как требует установленный DSPy.
Он вызывает существующий `EvaluatePrompt` с `DeterministicEvaluator` на output
prediction без повторного LLM-вызова. Применяются те же aggregate, structured
comparison и critical checks, без LLM judge.
При отсутствии critical failures score равен aggregate, а при наличии только
успешных critical checks — 1. Любой critical failure даёт **−N**, где N —
максимальная длина train/validation. Даже один такой failure делает среднее по
split отрицательным: остальные scores не превышают 1. Любой результат без
critical failures имеет неотрицательный score и выигрывает такое сравнение.
Это search objective адаптера, не domain metric; все метрики и critical details
остаются в feedback. GEPA всё ещё может вернуть candidate с нарушениями, если
безопасного варианта не найдено; candidate не получает approval или promotion.

### Конфигурация и credentials

```python
from prompt_optimizer.adapters.dspy.gepa_optimizer import GEPAConfig, GEPAOptimizer

config = GEPAConfig(
    model="provider/model",                 # LiteLLM provider/model notation
    reflection_model="provider/reflection-model",
    credential_env="PROVIDER_API_KEY",      # имя env, не сам секрет
    # reflection_credential_env="REFLECTION_API_KEY",  # по умолчанию тот же env
    max_metric_calls=100,
    max_tokens=4096,
    temperature=1.0,
    seed=0,
    # api_base="https://provider.example/v1",
    # reflection_api_base="https://provider.example/v1",
)
optimizer = GEPAOptimizer(config)
# optimizer.optimize(task, candidate_version="v002") вызывает API.
```

Секреты читаются только из env непосредственно перед optimize. Для reflection
можно задать другую модель, endpoint и переменную credentials. Endpoints
независимы; при None используются defaults провайдера. Config проверяет
provider/model notation, имена env, положительные целые budgets/tokens,
temperature `[0, 2]`, неотрицательный seed и http(s) endpoint.
Ограничения конкретной модели (например max_tokens для reasoning models)
проверяются DSPy/провайдером; выбирайте соответствующие параметры явно.

`GEPAConfigurationError(ValueError)` означает невалидный config;
`MissingCredentialsError(GEPAConfigurationError)` называет отсутствующий/пустой
env и возникает до создания LM. Невалидная candidate version вызывает
`ValueError` до внешнего boundary. `OptimizationError(RuntimeError)` из
application означает ошибку создания внешнего LM, выполнения GEPA, генерации,
feedback или mapping результата; исходное исключение сохраняется в `__cause__`.
Публичное сообщение не копирует provider exception text, который может содержать
секреты. При диагностике cause обращайтесь с ним как с потенциально чувствительными
данными. DSPy может подставлять failure score при ошибках: адаптер отслеживает
ошибки generation/feedback и не возвращает успешный результат после них.
Scored mismatches являются feedback, а не exceptions.

### Offline-пример

```python
from prompt_optimizer.adapters.fake_optimizer import FakeOptimizer
from prompt_optimizer.application import OptimizePrompt
from prompt_optimizer.domain import EvaluationCase, OptimizationTask, PromptVersion

train = EvaluationCase("train-1", "Неизвестная цена?", {"action": "escalate", "must_not_invent": True})
validation = EvaluationCase("validation-1", "Неизвестная услуга?", {"action": "escalate"})
task = OptimizationTask(
    task_id="demo-04",
    source_prompt=PromptVersion("clinic", "v001", "Не выдумывай цены."),
    train_cases=(train,), validation_cases=(validation,),
    dataset_id="clinic-demo", dataset_version="1",
)
result = FakeOptimizer(
    "Не выдумывай цены. При неизвестной услуге передай запрос оператору."
).optimize(task, candidate_version="v002")
assert result.candidate.parent_version == "v001"
assert result.candidate.status.value == "candidate"
print(result.candidate.text)
```

### Тесты и optional smoke

Unit tests mock LM/GEPA boundary и проверяют mapping, splits, feedback,
provenance, ошибки и общий порт. Дополнительно offline integration tests
запускают **настоящий GEPA.compile** с библиотечным `dspy.utils.DummyLM`:
evaluation, reflection, изменение instructions, mapping и обработку ошибок.
Сеть и платные LLM не используются.

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Smoke помечен `gepa_smoke` и требует отдельного opt-in. Без credentials он
пропускается с названием нужного env; даже с credentials обычный suite
пропускает его без `--run-gepa-smoke`. Реальные вызовы могут быть платными.
Для явного запуска сначала задайте секрет через окружение, затем модели и
запустите только smoke:

```powershell
# OPENAI_API_KEY должен быть задан вне исходников и файлов проекта.
$env:GEPA_CREDENTIAL_ENV = "OPENAI_API_KEY"
$env:GEPA_MODEL = "openai/your-selected-model"
$env:GEPA_REFLECTION_MODEL = "openai/your-selected-reflection-model"
# При необходимости: GEPA_REFLECTION_CREDENTIAL_ENV и GEPA_MAX_TOKENS.
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m gepa_smoke --run-gepa-smoke
```

В Session 04 real-provider smoke не запускался.

## End-to-end pipeline (Session 05)

Публичный API: `OptimizePrompt(optimizer, evaluation, thresholds=None).execute(task,
candidate_version=...) -> OptimizationPipelineResult`. `evaluation` — существующий
`EvaluatePrompt(client, evaluators)`, optimizer — любой `PromptOptimizer`.
Baseline оценивается на validation, затем optimizer получает исходный task
с train и validation, после проверки его контракта candidate оценивается
на том же объекте Dataset. GEPA search scores не используются для решения.
Registry, сохранение, promotion и CLI pipeline сюда не входят.

`CompareVersions(thresholds=None).execute(baseline, candidate) -> ComparisonResult`
сравнивает подробные EvaluationResult одного prompt name и dataset release
с одинаковыми case ids. Summary scores проверяются через существующий
`aggregate_metrics`; несогласованный summary или другая identity вызывает ValueError.

`RegressionThresholds(allowed_decrease={...}, minimum_scores={...})` из domain
задаёт абсолютные допустимые снижения и hard minimum scores в `[0, 1]`.
Конфигурация копируется, замораживается и валидируется; bool, NaN, infinity,
пустые имена и значения вне диапазона вызывают DomainValidationError.
Для каждой метрики, включая aggregate и safety scores, неуказанный threshold
равен **0**. Minimum применяется только к явно заданным метрикам.
Снижение ровно на threshold и score ровно на minimum допустимы.
Для delta используются decimal representations float scores без скрытого epsilon.

Правила решения:

- Любой critical failure candidate даёт **reject**, даже если он был в baseline.
- Снижение любой сопоставимой метрики больше её threshold или нарушение
  hard minimum даёт **reject**, независимо от aggregate.
- Missing metric, изменение набора cases, где метрика применима, либо её
  critical semantics дают `delta=None` и блокируют approve. Изменение coverage
  aggregate также проверяется по всем входящим некритическим метрикам.
  При отсутствии reject это **review**. Missing hard minimum даёт reject.
- **Approve** требует строго положительного сопоставимого aggregate delta,
  полной сопоставимости и прохождения всех safety/regression/minimum gates.
- Равенство, допустимое снижение без общего улучшения или отсутствие aggregate
  дают **review**. Встроенные deterministic checks и aggregation не изменены.

Результат содержит `task_id`, исходный `optimization` (OptimizationResult с
candidate и metadata), `baseline`, `candidate_evaluation`, `comparison` и
`recommendation`. В comparison доступны `before`, `after`, `deltas`, `gates`
(name, passed, reason) и recommendation. Gate `improvement` объясняет наличие
строгого улучшения; его false само по себе не означает reject.
Подробные evaluation сохраняют outputs, failed case ids и critical failures.
Рекомендация approve оставляет candidate в статусе candidate и не меняет source.

Перед candidate evaluation проверяются task id, prompt name, requested version,
candidate status, parent version, согласованность optimizer и dataset provenance.
Невалидные task/candidate_version отклоняются до вызовов. Ошибки выполнения
вызывают `PipelineError` со `stage`: `baseline_evaluation`, `optimizer`,
`optimizer_contract`, `candidate_evaluation` или `comparison`. Исходное исключение
доступно в `__cause__` (включая вложенный EvaluationError с case id).
Причина mismatch называет поле. Частичные pipeline results не возвращаются,
retries отсутствуют. Provider causes могут содержать чувствительные данные.

Полный воспроизводимый offline-пример из каталога с pyproject.toml:

```python
from prompt_optimizer.adapters.fake_llm import FakeLLMClient
from prompt_optimizer.adapters.fake_optimizer import FakeOptimizer
from prompt_optimizer.application import EvaluatePrompt, OptimizePrompt, CompareVersions
from prompt_optimizer.domain import (
    EvaluationCase, OptimizationTask, PromptVersion, RegressionThresholds,
)
from prompt_optimizer.domain.evaluation import DeterministicEvaluator

task = OptimizationTask(
    task_id="demo-05",
    source_prompt=PromptVersion("clinic", "v001", "Передавай неизвестные вопросы оператору.",
                                status="production"),
    train_cases=(EvaluationCase("train", "Неизвестная услуга?", {"action": "escalate"}),),
    validation_cases=(EvaluationCase("validation", "Неизвестная цена?",
                                    {"action": "escalate", "must_not_invent": True}),),
    dataset_id="clinic-demo", dataset_version="1",
)
# Ответы явно заданы по version и case id, не выводятся из expected.
# Старый FakeLLMClient({case_id: output}) продолжает работать.
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
assert result.candidate.status.value == "candidate"
assert result.baseline.failed_case_ids == ("validation",)
assert not result.candidate_evaluation.critical_failures
assert CompareVersions(thresholds).execute(
    result.baseline, result.candidate_evaluation,
).recommendation == result.recommendation
print(dict(result.comparison.deltas))
for gate in result.comparison.gates:
    print(gate.name, gate.passed, gate.reason)
```

Проверка Session 05: полный suite через `.venv`, без real-provider smoke.

## Prompt Registry (Session 06)

`PromptRepository` из `prompt_optimizer.ports` — injected port. Реализация:
`FilesystemPromptRepository(root)` из `prompt_optimizer.adapters.filesystem_registry`.
Root всегда задаёт вызывающий код. Domain/application не выполняют filesystem I/O.

Публичные методы repository:

- `create(prompt, provenance=None)` сохраняет только candidate;
- `bootstrap(prompt)` регистрирует исходный production только при пустой истории
  данного prompt, без parent; другие prompt names могут уже существовать;
- `read(name, version)`, `list(name)`, `production(name)` возвращают immutable
  PromptVersion; list имеет детерминированный лексикографический порядок версий,
  production возвращает `None`, если production нет;
- `next_version(name)` вычисляет имя, но не резервирует его;
- `approve(name, version)`, `reject(name, version)`, `promote(name, version)`
  выполняют отдельные явные действия;
- `history(name)` возвращает immutable tuple всех `StatusTransition` этого prompt
  в порядке операций; `provenance(name, version)` возвращает `RegistryProvenance`.

Application API из `prompt_optimizer.application`:
`SaveCandidate(repository).execute(pipeline_result)` сохраняет candidate с **validation
scores**, task id и OptimizationResult.metadata. Оно не меняет исходный pipeline result.
`ApprovePrompt`, `RejectPrompt`, `PromotePrompt` принимают repository и вызываются через
`.execute(name, version)`. Эти use cases зависят только от port. SaveCandidate допустим
при любой recommendation: рекомендация не выполняет approval/rejection/promotion.
OptimizePrompt остаётся прежним и возвращает candidate без сохранения.

### Lifecycle и неизменяемость

| Явная операция | Исходный статус | Новый статус |
| --- | --- | --- |
| create | новая identity | candidate |
| bootstrap | пустая история prompt | production |
| approve | candidate | approved |
| reject | candidate | rejected |
| promote | approved | production |
| часть promote | прежняя production | archived |

Повторный approve/reject/promote является ошибкой, а не no-op. Другие переходы
запрещены; candidate, rejected и archived нельзя продвигать. Публичной операции
archive нет. Архивирование и promotion коммитятся вместе. Максимум одна production
на prompt; разные prompt names независимы.

create никогда не заменяет существующую identity (включая case collisions).
Текст, name/version, parent, optimizer, dataset provenance, scores, created_at и
registry provenance после создания не меняются. Lifecycle меняет только status
и добавляет event. Каждая операция возвращает новый immutable snapshot; прежние
объекты сохраняют свои статусы. Parent должен уже существовать для того же prompt.
Чтение проверяет порядок создания родителей и отклоняет циклы, missing/self-parent,
повреждённые или неполные histories.

### Формат хранения

Используется эквивалент пар md/json: **один атомарный snapshot на prompt**:
`<root>/prompts/<name.casefold()>.json`. Это позволяет одной заменой файла менять
статусы двух версий при promotion, без промежуточных двух production и без общего
transaction framework. Все версии и события находятся в этом файле; текст хранится
в JSON-строке UTF-8 и восстанавливается побайтно после UTF-8 encoding, включая
Unicode, пробелы, CRLF/LF и конечные переносы.

Schema 1 имеет ровно поля `schema`, `name`, `records`, `history`.
`records` — mapping version -> `{prompt, provenance}`. `prompt` содержит **все** поля
PromptVersion: name, version, text, status, parent_version, optimizer, dataset_id,
dataset_version, scores, created_at (ISO 8601 с timezone offset).
`provenance` содержит `task_id` (строка или null) и `metadata` (finite JSON mapping).
`history` — массив `{operation, version, previous, status, at}`;
previous равен null при create/bootstrap, далее содержит предыдущий статус;
at — timezone-aware ISO 8601. Архивирование предшествует promotion в одном commit.
При чтении журнал проигрывается целиком и проверяется соответствие текущим статусам.
Неизвестная schema, лишние/недостающие поля, повторные JSON keys, nonfinite values,
невалидные domain-поля и неполные records отклоняются, в том числе при list.

Registry contracts/errors импортируются из `prompt_optimizer.domain.registry`.
RegistryProvenance копирует и замораживает JSON; запрещает на любой глубине ключи,
содержащие secret/password/credential/api_key/access_token/authorization
(без учёта регистра, дефис нормализуется к underscore). Metadata не редактируется
и не обрезается молча. Вызывающий код обязан передавать только несекретные данные:
произвольную секретную строку под нейтральным ключом автоматически распознать нельзя.
Не передавайте credentials в task id, prompt text или другие registry поля.

### Версии и ошибки

Генерация: `v001`, `v002`, ... `v999`, `v1000`, ...; номер равен максимальному
существующему номеру + 1, gaps не заполняются. Пустой registry даёт v001.
Поддерживается строго canonical `v` + минимум три ASCII цифры, положительный номер,
без дополнительных начальных нулей. Неподдерживаемые safe opaque versions можно
сохранять/читать, но next_version явно завершается ошибкой. Domain identifiers
остаются opaque. candidate_version по-прежнему явный аргумент OptimizePrompt.
Если между next_version и create другой процесс займёт имя, create вернёт duplicate.

Ошибки имеют operation/name/version/reason:
`VersionNotFoundError`, `DuplicateVersionError`, `CorruptRegistryError`,
`RegistryConflictError`, `InvalidTransitionError` наследуют `RegistryError`.
Filesystem I/O и validation failures обёрнуты в RegistryError с исходной причиной
в `__cause__`; corruption, missing и I/O различаются. Duplicate — самостоятельная
business error без underlying exception. Неверные переходы имеют ValueError cause.
Ошибки не возвращают успешный или частичный результат. Retries отсутствуют.

### Запись, concurrency и ограничения

Все операции, включая чтение, получают fail-fast interprocess lock через атомарный
mkdir `<root>/prompts/<name.casefold()>.lock`. Все cooperating repository instances
с одним root сериализованы для данного prompt; конфликт вызывает RegistryConflictError.
Проверка duplicate выполняется внутри lock. Разные prompt names используют разные locks.
Snapshot полностью валидируется, сериализуется во временный файл в той же директории,
flush/fsync завершаются до `os.replace`. До replace сохранённый snapshot не меняется;
после replace виден целый новый snapshot. Incomplete temp-файлы не являются records.
Ошибка до commit сохраняет все прежние версии. Ошибка cleanup после commit явно
сообщает, что commit мог состояться: после устранения lock нужно прочитать состояние.

Гарантии рассчитаны на локальную filesystem Windows/POSIX с атомарным replace и mkdir,
cooperating processes и доверенную директорию. Нет гарантий durability при потере питания
(directory fsync не выполняется), для сетевых filesystem или внешнего редактирования
файлов. Аварийное завершение может оставить .lock или .tmp; после подтверждения, что
writer завершён, оператор удаляет только соответствующий stale lock/temp. Автоматического
recovery/retries нет; JSON при этом остаётся старым или новым целым snapshot.
Все версии одного prompt переписываются в snapshot, поэтому решение предназначено для MVP.

Adapter запрещает traversal, absolute paths, separators, Windows reserved filenames,
control characters, опасные символы и trailing dot/space. Casefold filename плюс
проверка исходного name/version исключают Windows case collisions на любой платформе.
Symlink/junction/reparse-point в root, его предках и используемых путях отклоняются;
adapter не разрешает их даже внутрь root. Root должен быть доверенным: защита от
злоумышленника, одновременно подменяющего директории во время операции, не заявляется.

### Полный offline lifecycle

Запустите из каталога с pyproject.toml через `.venv`; никаких API credentials не требуется:

```python
from tempfile import TemporaryDirectory
from prompt_optimizer.adapters.filesystem_registry import FilesystemPromptRepository
from prompt_optimizer.adapters.fake_llm import FakeLLMClient
from prompt_optimizer.adapters.fake_optimizer import FakeOptimizer
from prompt_optimizer.application import (
    EvaluatePrompt, OptimizePrompt, SaveCandidate, ApprovePrompt, PromotePrompt,
)
from prompt_optimizer.domain import PromptVersion, EvaluationCase, OptimizationTask
from prompt_optimizer.domain.evaluation import DeterministicEvaluator

with TemporaryDirectory() as root:
    repository = FilesystemPromptRepository(root)
    repository.bootstrap(PromptVersion(
        "clinic", "v001", "Передавай неизвестные вопросы оператору.", status="production",
    ))
    source = repository.production("clinic")
    version = repository.next_version("clinic")
    train = EvaluationCase("train", "Неизвестная услуга?", {"action": "escalate"})
    validation = EvaluationCase("validation", "Неизвестная цена?",
                                {"action": "escalate", "must_not_invent": True})
    task = OptimizationTask("demo-06", source, (train,), (validation,), "clinic-demo", "1")
    client = FakeLLMClient({}, version_responses={
        ("v001", "validation"): {"action": "answer"},
        (version, "validation"): {"action": "escalate"},
    })
    result = OptimizePrompt(
        FakeOptimizer("Не выдумывай цены. Передавай неизвестные вопросы оператору."),
        EvaluatePrompt(client, [DeterministicEvaluator()]),
    ).execute(task, candidate_version=version)
    assert result.recommendation.value == "approve"
    assert len(repository.list("clinic")) == 1  # pipeline ничего не сохраняет
    candidate = SaveCandidate(repository).execute(result)
    assert candidate.status.value == "candidate"
    assert candidate.scores == result.candidate_evaluation.scores
    assert repository.production("clinic").version == "v001"
    ApprovePrompt(repository).execute("clinic", version)
    PromotePrompt(repository).execute("clinic", version)
    reopened = FilesystemPromptRepository(root)
    assert reopened.production("clinic").version == version
    assert reopened.read("clinic", "v001").status.value == "archived"
    assert reopened.provenance("clinic", version).task_id == "demo-06"
    assert [e.operation for e in reopened.history("clinic")] == [
        "bootstrap", "create", "approve", "archive", "promote",
    ]
```

Task 07 (CLI commands/report writers) реализована; документация ниже. Real-provider gepa_smoke
не запускается. Полная проверка: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`.

## Reports + CLI (Session 07)

CLI сохраняет прежние `--help`, `--version`, entry point `prompt-opt` и
`python -m prompt_optimizer`. Все команды требуют `--registry-root` и
`--prompt-name`; кроме `versions`, также `--prompt-version`.

- `bootstrap --prompt-file FILE`: UTF-8 исходный production, только пустая история.
- `evaluate --dataset FILE --dataset-id ID --dataset-version VERSION --client fake
  --responses FILE --report FILE`: подробная evaluation.
- `optimize --train FILE --validation FILE --dataset-id ID --dataset-version VERSION
  --task-id ID --client fake --responses FILE --optimizer fake --candidate-file FILE
  --report FILE [--candidate-version VERSION]`: validation pipeline и SaveCandidate.
  Если candidate-version отсутствует, используется repository.next_version.
- `versions`: JSON строки с version, status, parent_version и provenance.
- `approve`, `reject`, `promote`: отдельные явные application use cases.

| Exit code | Значение |
|---|---|
| 0 | Успех; evaluate без failed cases; optimize с рекомендацией approve |
| 1 | Ошибка входных файлов, исполнения, registry/storage или report |
| 2 | Отсутствующие/неизвестные CLI аргументы или неверный choice |
| 3 | Evaluation с failed cases либо optimization recommendation reject |
| 4 | Optimization recommendation review |

Коды 3/4 означают завершённый расчёт с записанным report; optimize сохраняет
candidate и при reject/review. Рекомендация approve не является approval или
deployment. Production меняется только явным promote после approve.
CLI сообщает безопасную стадию ошибки, не печатает raw exception/provider cause.
Программные use cases и ReportWriteError сохраняют исходные причины в __cause__.

### Reporting API и versioned JSON contract 1.0

`prompt_optimizer.ports.ReportWriter.write(report, destination)` — injected port.
Публичные функции `build_report`, `write_report`, `terminal_summary`, `json_value`
находятся в `prompt_optimizer.application.reporting`; adapter —
`prompt_optimizer.adapters.json_report.JsonReportWriter`.
`write_report(result, path, writer=writer, started_at=aware_datetime,
completed_at=aware_datetime)` принимает EvaluationResult или OptimizationPipelineResult.
CLI также допускает `main(argv, report_writer=writer)` для embedding/testing.

Общие поля JSON: `schema_version: "1.0"`, `report_type`, `timestamps` с
`started_at` и `completed_at` (ISO 8601 с timezone, границы workflow).
Evaluation report содержит `evaluation`: prompt_name/version, dataset_id/version,
cases, scores, failed_case_ids, critical_failures. Каждый case содержит case_id,
полный output и metrics (name, score, passed, critical, details).
Optimization report содержит task_id, prompt_name, baseline_version,
candidate_version, dataset_id/version, optimization (task_id, полный candidate
с created_at и metadata optimizer), baseline и candidate_evaluation в том же
подробном evaluation формате; before/after scores, deltas (число либо null),
gates (name, passed, reason), recommendation (approve/reject/review).
Все dataclass-поля сериализуются; immutable mappings/tuples становятся JSON
objects/arrays, enums — значения. Unicode сохраняется, NaN/Infinity и неизвестные
типы отклоняются. Breaking schema changes потребуют нового schema_version.

Report — отдельный артефакт, не registry metadata. Parent directory должен
существовать. Существующий report **заменяется**: UTF-8 temp file в той же
директории, flush/fsync, atomic os.replace. Ошибка до replace сохраняет старый
report. Report path выбирайте отдельно от registry snapshots и входных файлов.
Report может содержать prompt text и model outputs: не передавайте credentials
в эти данные или optimizer metadata. Автоматического распознавания секретов
в произвольном тексте нет.

Optimize сначала завершает pipeline, затем SaveCandidate, затем report.
Если report не записан после сохранения, CLI возвращает 1 и сообщает сохранённую
identity и статус candidate. История не откатывается; не повторяйте optimization
автоматически. Через versions проверьте фактическое состояние. Нет retries или
общей транзакции report+registry. Гарантии atomic replace рассчитаны на локальную
доверенную файловую систему; дополнительные ограничения registry описаны выше.

### Полный offline CLI пример

Все файлы ниже — UTF-8. Сохраните `source.txt` с текстом
«Передавай неизвестные вопросы оператору», `candidate.txt` с текстом
«Не выдумывай цены. Передавай неизвестные вопросы оператору».
Сохраните `train.jsonl` и `validation.jsonl` с одинаковой одной строкой
(пересечение ids между splits разрешено):

```json
{"id":"unknown","input":"Неизвестная цена?","expected":{"action":"escalate","must_not_invent":true}}
```

Сохраните `responses.json`; ответы задаются явно по version/case id и не
выводятся из expected. Train не вызывает FakeLLMClient в FakeOptimizer.

```json
{
  "v001": {"unknown": {"action": "answer"}},
  "v002": {"unknown": {"action": "escalate"}},
  "v003": {"unknown": {"action": "escalate"}}
}
```

PowerShell из каталога pyproject.toml (с новым пустым `demo-registry`):

```powershell
$py = '.\.venv\Scripts\python.exe'
$identity = @('--registry-root', 'demo-registry', '--prompt-name', 'clinic')
$dataset = @('--dataset-id', 'demo', '--dataset-version', '1', '--client', 'fake', '--responses', 'responses.json')
& $py -m prompt_optimizer bootstrap @identity --prompt-version v001 --prompt-file source.txt
& $py -m prompt_optimizer evaluate @identity --prompt-version v001 @dataset --dataset validation.jsonl --report evaluation.json
# evaluate вернёт 3: baseline имеет failed case; report при этом записан.
& $py -m prompt_optimizer optimize @identity --prompt-version v001 @dataset --train train.jsonl --validation validation.jsonl --task-id demo-07 --optimizer fake --candidate-file candidate.txt --candidate-version v002 --report optimization.json
& $py -m prompt_optimizer versions @identity
& $py -m prompt_optimizer approve @identity --prompt-version v002
& $py -m prompt_optimizer promote @identity --prompt-version v002
# v002 production, v001 archived. Создадим отдельный candidate и явно отклоним:
& $py -m prompt_optimizer optimize @identity --prompt-version v001 @dataset --train train.jsonl --validation validation.jsonl --task-id demo-reject --optimizer fake --candidate-file candidate.txt --candidate-version v003 --report separate.json
& $py -m prompt_optimizer reject @identity --prompt-version v003
```

CLI этой сессии поддерживает только явно выбранные fake client/optimizer.
GEPA adapter остаётся доступным через существующий Python API; настройка реального
provider CLI не добавлена. Платные API и real-provider gepa_smoke не запускаются.
Offline CLI integration проверяет bootstrap/evaluate/optimize/versions/approve/
promote, production replacement, отдельный reject, validation scores и provenance.

## FastAPI (Session 08)

`prompt_optimizer.interfaces.http.create_app(HttpDependencies(...))` создаёт
изолированное приложение. Зависимости передаются явно: `PromptRepository`,
`EvaluatePrompt`, `OptimizePrompt` и `OptimizationResultStore`. CLI и HTTP используют
одни application services, включая `SaveCandidate`, `ApprovePrompt`, `RejectPrompt`,
`PromotePrompt` и внутренний `CompareVersions` pipeline. Новых registry или
prompt domain models нет; Pydantic DTO находятся только на HTTP boundary.

В TestClient можно заменить весь bundle через
`app.dependency_overrides[get_dependencies] = lambda: dependencies`, где
`get_dependencies` импортируется из `prompt_optimizer.interfaces.http`.
Каждый app получает свой store; намеренно общий store можно передать явно.
Handlers синхронные: FastAPI выполняет блокирующие use cases в thread pool.
Импорт модулей не создаёт registry и не запускает сервер.

Composition root `prompt_optimizer.http_runtime.build_offline_app` принимает
`OfflineConfiguration(registry_root, candidate_text, version_responses)` и собирает
filesystem repository, явно настроенные `FakeLLMClient`, `FakeOptimizer`, deterministic
evaluation и отдельный in-memory store. Responses задаются по `(prompt_version, case_id)`;
они никогда не выводятся из expected. Real-provider конфигурация в HTTP не добавлена.
Registry root, client/optimizer и необязательный destination для `ReportWriter` выбирает
сервер. HTTP request не принимает filesystem paths или credentials.

Установка и запуск из каталога с `pyproject.toml`:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
# После bootstrap примера ниже:
$env:PROMPT_REGISTRY_ROOT = Join-Path (Get-Location) 'http-demo-registry'
.\.venv\Scripts\python.exe -m uvicorn prompt_optimizer.http_runtime:create_demo_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

`create_demo_app` — factory с фиксированными offline responses для примера ниже.
Для своих cases используйте `build_offline_app` с собственной явной конфигурацией.
OpenAPI: `GET /openapi.json`, интерактивные schemas: `/docs`.

| Endpoint | Success response |
|---|---|
| `POST /evaluations` | 200, evaluation report schema 1.0 |
| `POST /optimizations` | 201, `{ "id": "<uuid>", "report": <optimization report 1.0> }` |
| `GET /optimizations/{id}` | 200, тот же сохранённый response, без повторного pipeline |
| `GET /prompts/{name}/versions` | 200, массив version/status/parent_version/provenance |
| `POST /prompts/{name}/{version}/approve` | 200, полный PromptVersion snapshot со статусом approved |
| `POST /prompts/{name}/{version}/reject` | 200, snapshot со статусом rejected |
| `POST /prompts/{name}/{version}/promote` | 200, snapshot со статусом production |

Evaluation body: `prompt_name`, `prompt_version`, `dataset`.
Lifecycle actions принимают запрос без body, `null` или пустой объект `{}`;
любые поля в body отклоняются с 422.
Optimization body: `prompt_name`, `prompt_version`, `task_id`, отдельные `train`
и `validation`, необязательный `candidate_version`. Каждый dataset содержит
обязательные `id`, `version`, непустой массив `cases`. Id/version обоих splits
должны совпадать. Cases используют существующий contract `id`, `input`, `expected`,
необязательные `context` и `tags`. Duplicate case ids внутри split запрещены;
пересечение между splits разрешено. Неизвестные поля, неверные типы, пустые identities,
NaN/Infinity и вложенные credential-bearing keys отклоняются с 422.
При отсутствии candidate_version сервер явно передаёт repository.next_version
в OptimizePrompt; конкурентный duplicate завершается 409 без retries.

HTTP reports сохраняют полный versioned contract 1.0 из Session 07: Unicode,
outputs, metric details, scores, failures, before/after/deltas, gates, optimizer
metadata и timezone-aware timestamps. Pydantic response schemas не обрезают поля.
Recommendation approve/reject/review и evaluation с failed cases — успешный расчёт
(200/201), а не technical failure. Любая рекомендация оставляет candidate в статусе
candidate. Approval и promotion всегда отдельные действия.

### Воспроизводимый offline HTTP lifecycle

Используйте новый пустой `http-demo-registry`. Перед запуском сервера выполните
bootstrap через существующий repository (UTF-8 Python source):

```powershell
.\.venv\Scripts\python.exe -c "from prompt_optimizer.adapters.filesystem_registry import FilesystemPromptRepository; from prompt_optimizer.domain import PromptVersion; FilesystemPromptRepository('http-demo-registry').bootstrap(PromptVersion('clinic', 'v001', 'Передавай неизвестные вопросы оператору.', status='production'))"
```

Factory использует candidate text
«Не выдумывай цены. Передавай неизвестные вопросы оператору.» и ровно следующие
явные fake outputs; train не вызывает LLM в FakeOptimizer:

```json
{
  "v001": {"unknown": {"action": "answer"}},
  "v002": {"unknown": {"action": "escalate", "note": "Проверено"}},
  "v003": {"unknown": {"action": "escalate"}}
}
```

После запуска сервера выполните во втором PowerShell из того же каталога:

```powershell
$base = 'http://127.0.0.1:8000'
$release = @{
    id = 'demo'; version = '1'
    cases = @(@{
        id = 'unknown'; input = 'Неизвестная цена?'
        expected = @{action = 'escalate'; must_not_invent = $true}
    })
}
$evaluation = @{
    prompt_name = 'clinic'; prompt_version = 'v001'; dataset = $release
} | ConvertTo-Json -Depth 20
$before = Invoke-RestMethod -Method Post -Uri "$base/evaluations" -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($evaluation))
# 200: before.evaluation.failed_case_ids содержит unknown.
$request = @{
    prompt_name = 'clinic'; prompt_version = 'v001'; task_id = 'http-demo-08'
    candidate_version = 'v002'; train = $release; validation = $release
}
$body = $request | ConvertTo-Json -Depth 20
$run = Invoke-RestMethod -Method Post -Uri "$base/optimizations" -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
# 201: run.id — UUID; run.report.recommendation == approve; v002 ещё candidate.
$saved = Invoke-RestMethod -Uri "$base/optimizations/$($run.id)"
$versions = Invoke-RestMethod -Uri "$base/prompts/clinic/versions"
Invoke-RestMethod -Method Post -Uri "$base/prompts/clinic/v002/approve"
Invoke-RestMethod -Method Post -Uri "$base/prompts/clinic/v002/promote"
# Теперь v002 production, v001 archived; сохранённый run остаётся историческим.
$request.candidate_version = 'v003'
$request.task_id = 'http-demo-reject'
$body = $request | ConvertTo-Json -Depth 20
Invoke-RestMethod -Method Post -Uri "$base/optimizations" -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
Invoke-RestMethod -Method Post -Uri "$base/prompts/clinic/v003/reject"
Invoke-RestMethod -Uri "$base/prompts/clinic/versions"
```

Краткий response optimization (полный report включает остальные поля schema 1.0):

```json
{"id":"<uuid>","report":{"schema_version":"1.0","report_type":"optimization","task_id":"http-demo-08","prompt_name":"clinic","baseline_version":"v001","candidate_version":"v002","dataset_id":"demo","dataset_version":"1","recommendation":"approve"}}
```

Versions entry для сохранённого candidate:

```json
{"version":"v002","status":"candidate","parent_version":"v001","provenance":{"task_id":"http-demo-08","metadata":{"offline":true}}}
```

### Ошибки и частично завершённый workflow

Все errors используют стабильный contract; validation errors не отражают input
или Pydantic ctx, provider exceptions, traceback и внутренние paths не возвращаются:

```json
{"error":{"code":"validation_error","message":"Request does not satisfy the HTTP contract.","saved_candidate":null,"state_requires_verification":false}}
```

| HTTP status | error.code | Значение |
|---|---|---|
| 422 | validation_error | Неверный request contract или JSON |
| 404 | not_found | Неизвестный optimization id, prompt или version |
| 409 | duplicate_identity | Версия уже существует |
| 409 | invalid_transition | Например, promote без approval |
| 409 | lock_conflict | Registry lock занят |
| 500 | corrupt_storage | Повреждён registry; требуется проверка оператором |
| 500 | registry_failure | Ошибка registry I/O; состояние требует проверки |
| 500 | execution_failure | Pipeline, evaluation или чтение result store завершились ошибкой |
| 500 | result_storage_failure | Candidate сохранён, но report/result storage не завершён |

Другие HTTP routing errors также имеют эту schema с code `http_error`.
Исходные причины сохраняются в `__cause__` программных исключений.
Порядок optimization: pipeline -> SaveCandidate (validation scores, task id,
optimizer provenance) -> report -> result store -> 201. Подробный report не попадает
в registry metadata. Необязательный `report_writer`/`report_destination` в dependencies
использует существующий `write_report`/`ReportWriter`; по умолчанию отдельный файл
не записывается. `JsonReportWriter` доступен для server-configured destination.

Если запись report/result не удалась после SaveCandidate, 500 содержит
`saved_candidate: {"name":"clinic","version":"v002","status":"candidate"}`.
Registry не откатывается, pipeline автоматически не повторяется. Проверьте versions;
доступного optimization id при таком failure может не быть.
При registry cleanup failure после commit возвращается `registry_failure` с
`state_requires_verification: true`: commit мог состояться, отсутствие изменений
не гарантируется. После устранения lock проверьте registry перед дальнейшими действиями.

Results store — только память одного app/process. Результаты теряются при рестарте;
несколько workers не имеют общего store. Registry продолжает хранить prompts и provenance.
Для этого MVP запускайте один worker. API предназначен для локального использования
на loopback без authentication; external deployment, shared result persistence, очереди,
background jobs и real providers не входят в задачу 08.

`tests/test_http.py` проверяет каждый endpoint, OpenAPI, validation, ошибки и полный
offline lifecycle, production replacement, отдельный reject, рекомендации, точность
report schema 1.0, injected dependencies, изоляцию apps, thread-pool execution и failures
до/после сохранения candidate. Полный offline набор (gepa_smoke не включается):

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Полный прогон после Session 08: **384 passed, 2 skipped**; 44 новых HTTP tests.
Есть одно предупреждение Starlette о deprecated интеграции TestClient с httpx;
установленная комбинация проходит тесты. Установка `-e ".[dev]"` проверена в `.venv`.

## Agent tool (Session 09)

`prompt_optimizer.interfaces.agent_tool.AgentTools` — независимый от agent SDK
интерфейс с двумя callable: `optimize_prompt` и `get_optimization_report`.
`tool_definitions()` возвращает их описания и JSON Schemas input/output/error.
Pydantic используется только на внешних tool/HTTP boundaries; domain models и
application pipeline остаются прежними. Никакой сервер не запускается при import.

Архитектура: `AgentTools -> OptimizationBackend` (узкий port из
`prompt_optimizer.ports.agent_tool`) -> `HttpOptimizationBackend` -> HTTP API задачи 08
-> существующие application services. Port разрешает только `resolve_source`,
`optimize`, `candidate_status`, `get_report`; lifecycle writes в нём отсутствуют.
Tool не вызывает CLI main и не создаёт второй pipeline или registry.

`DatasetResolver.resolve(id, version)` возвращает два существующих domain `Dataset`.
`FileDatasetResolver(sources, loader)` получает mapping `(id, version) -> (train_source,
validation_source)` от host/server, использует `DatasetLoader` и `load_train_validation`.
Agent выбирает только identity. Tool проверяет фактические identities, типы,
непустые splits и уникальные case ids; пересечение ids между splits допустимо.
Cases и ответы не генерируются из expected.

HTTP adapter настраивается через `HttpBackendConfiguration(base_url, timeout_seconds=30)`.
Base URL, paths, credentials и runtime dependencies не являются agent arguments.
HTTP client/transport и resolver injectable. `httpx` теперь runtime dependency
(раньше уже устанавливался в dev); иных новых dependencies нет. Adapter без injected
client владеет своим client: после использования вызовите `backend.close()`.
Injected client закрывает host. Default transport не повторяет requests; injected
transport/client также должен соблюдать запрет automatic retries.

### Публичные contracts

Input `OptimizePromptRequest`: обязательные `prompt_name`, `dataset_id`,
`dataset_version`; ровно один `source_version` или `production: true`.
`source_version: null` допустим только с production. Optional `candidate_version`
передаётся по правилам API задачи 08; без него сервер вызывает `next_version`.
Tool генерирует task id. Unknown fields, неверные типы, пустые identities и
NaN/Infinity отклоняются. JSON Schema отражает взаимоисключение source selectors.

```json
{"prompt_name":"clinic","production":true,"dataset_id":"demo","dataset_version":"1","candidate_version":"v002"}
```

Source production разрешается через новый минимальный read-only endpoint
`GET /prompts/{name}/production`: 200 — прежняя schema `PromptResponse`,
404 — прежняя schema `ErrorResponse` с `not_found`. Endpoint использует
`repository.production(name)`, JSON registry напрямую не читается. Existing
`GET /prompts/{name}/versions` различает unknown prompt/source version и возвращает
фактический статус candidate после optimization; прежние endpoints совместимы.

Output `OptimizePromptResponse`:

| Поле | Значение |
|---|---|
| `candidate` | name/version/status из результата и отдельного registry read |
| `source_version`, `dataset_id`, `dataset_version` | Разрешённые identities |
| `before`, `after`, `deltas` | Существующие comparison metrics без пересчёта |
| `gates`, `violations` | Исходные comparison gates и их подмножество `passed=false` |
| `baseline_failures`, `candidate_failures` | failed_case_ids и critical_failures каждого split evaluation |
| `recommendation` | approve/reject/review — все являются успешным расчётом |
| `report` | id, schema_version 1.0, retrieval_tool, GET api_path, lifetime |

`violations` включают failed improvement gate: это может означать `review`,
а не обязательно `reject`. Tool не вводит новые правила CompareVersions.
Status читается после POST и может отражать независимое действие человека;
concurrent snapshot не гарантирует неизменность статуса в будущем.

`get_optimization_report` принимает только `{"report_id":"<id>"}` и возвращает
полный `OptimizationResponse` задачи 08: `{id, report}`. Summary является проекцией
этого report, полный report сохраняет outputs, scores, details, gates, provenance,
Unicode и timezone-aware timestamps. Boundary отклоняет неизвестные поля,
malformed/duplicate-key JSON, nonfinite numbers и несогласованные identities/details.
GET report не вызывает optimizer или LLM. Report ids живут только в in-memory store
исходного HTTP app/process; restart, другой worker или другая app instance могут
сделать их недоступными. Потерянный id никогда не запускает pipeline повторно.
Подробный report не сохраняется в registry metadata.

### Errors и неизвестный outcome

Python методы бросают `ToolError` с безопасным сообщением и исходным cause.
`tools.invoke(name, arguments)` возвращает успешный DTO либо строгий error DTO:

```json
{"error":{"code":"timeout","message":"HTTP request timed out; after POST the outcome is unknown. Verify registry before another optimization.","saved_candidate":null,"state_requires_verification":true,"outcome_unknown":true}}
```

| error.code | Причина |
|---|---|
| `input_validation` | Неверный tool input или HTTP 422 |
| `unknown_dataset` | Dataset release не настроен |
| `invalid_dataset` | Ошибка loader/resolver, пустой split, duplicate case ids |
| `dataset_identity_mismatch` | Фактический dataset release отличается |
| `unknown_prompt`, `unknown_version`, `production_not_found` | Не найден соответствующий source |
| `report_not_found` | Неизвестный/потерянный report id |
| `duplicate_identity`, `lock_conflict` | HTTP registry conflict |
| `corrupt_storage`, `registry_failure` | Corruption или registry I/O/commit failure |
| `execution_failure`, `result_storage_failure` | Technical workflow/result failure |
| `http_transport_failure`, `timeout` | Ошибка транспорта или deadline |
| `invalid_api_response` | Malformed/невалидный или несогласованный ответ API |

Raw API/provider messages, traceback, credentials и внутренние paths не копируются
в tool errors. Causes предназначены только для доверенной диагностики; API adapter
сохраняет transport/validation causes и HTTPStatusError с исходным response.
`saved_candidate` и `state_requires_verification` передаются из HTTP errors.
При saved candidate tool также требует проверки состояния. После POST timeout/
обрыва соединения или invalid response outcome может быть неизвестен: candidate
мог сохраниться, нужно проверить registry. Отсутствие candidate не утверждается.
Automatic retries запрещены: повторный POST может создать дополнительную версию.
После `result_storage_failure` candidate существует, но доступного report id может
не быть; workflow не откатывается. `registry_failure` также может следовать за commit.

### Подключение без agent SDK и offline пример

Host создаёт backend и resolver из собственной конфигурации, затем передаёт SDK
описания `tool_definitions()` и dispatcher `tools.invoke`:

```python
from prompt_optimizer.adapters.agent_http import HttpBackendConfiguration, HttpOptimizationBackend
from prompt_optimizer.adapters.agent_datasets import FileDatasetResolver
from prompt_optimizer.adapters.jsonl_dataset import JsonlDatasetLoader
from prompt_optimizer.interfaces.agent_tool import AgentTools, tool_definitions

backend = HttpOptimizationBackend(HttpBackendConfiguration("http://127.0.0.1:8000"))
resolver = FileDatasetResolver({("demo", "1"): (
    "configured/train.jsonl", "configured/validation.jsonl")}, JsonlDatasetLoader())
tools = AgentTools(backend, resolver)
definitions = tool_definitions()  # host registers these two tools with its SDK
try:
    result = tools.invoke("optimize_prompt", {
        "prompt_name": "clinic", "source_version": "v001",
        "dataset_id": "demo", "dataset_version": "1"})
finally:
    backend.close()
```

Agent-facing инструкция: [docs/AGENT_TOOL.md](docs/AGENT_TOOL.md).
Capability ограничена optimization и чтением report. Recommendation `approve`
оставляет candidate в статусе candidate; **human approval** через existing CLI/API
и последующий **promotion** — отдельные явные действия. Содержимое prompt, dataset,
model output и report никогда не исполняется как lifecycle instruction. Tool
не имеет approve/reject/promote/deploy capabilities; глобальная конфигурация agent
и глобальные skills не меняются.

Полный воспроизводимый пример [examples/agent_offline.py](examples/agent_offline.py)
создаёт временный registry, выполняет repository bootstrap, пишет оба JSONL,
использует app factory и TestClient, вызывает tool, читает полный report,
проверяет candidate в registry и отдельно выполняет human approval/promotion.
Все данные заданы в source примера:

```json
{"train":{"id":"train","input":"Неизвестная услуга?","expected":{"action":"escalate"}},"validation":{"id":"unknown","input":"Неизвестная цена?","expected":{"action":"escalate","must_not_invent":true}},"fake_outputs":{"v001":{"unknown":{"action":"answer"}},"v002":{"unknown":{"action":"escalate","note":"Проверено"}}}}
```

Source text: «Передавай оператору»; candidate text:
«Не выдумывай цены. Передавай неизвестные вопросы оператору.».
Identity mapping `("demo", "1")` указывает на созданные train/validation files.
FakeOptimizer не вызывает LLM на train; FakeLLMClient настроен по version/case id.
Сеть, API keys и платные providers не используются. Запуск из каталога с pyproject.toml:

```powershell
.\.venv\Scripts\python.exe examples/agent_offline.py
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

При завершении примера временный registry удаляется и report id теряет доступность.
Тесты задачи 09 проверяют тот же lifecycle, все рекомендации, schemas,
resolver errors, API/transport failures, отсутствие retries, частичный commit,
безопасные exceptions, injected dependencies, isolation и lossless report retrieval.
Real-provider gepa_smoke не запускается. Внешние integrations не добавлены.

## Failure analyzer (Session 10)

Добавлен явный controlled loop: trace → injected sanitization → FailureCase →
deterministic analysis → pending draft → отдельный human review → явный экспорт
нового Dataset release. Изменение draft снимает approval; pending/rejected drafts
экспортировать нельзя. Failed output не используется как expected. HTTP API,
agent tools, report 1.0 и prompt lifecycle сохранены.

Публичные contracts, ошибки, privacy/persistence ограничения и порядок review:
[docs/FAILURE_ANALYZER.md](docs/FAILURE_ANALYZER.md).
Полный воспроизводимый сценарий с synthetic-only sanitizer, FakeOptimizer и
FakeLLMClient: [examples/failures_offline.py](examples/failures_offline.py).
Пример явно подключает release к resolver, отдельно вызывает optimize_prompt,
читает report 1.0 и проверяет неизменность production.

```powershell
.\.venv\Scripts\python.exe examples/failures_offline.py
```

Итог Session 10: **516 passed, 2 skipped**, 44 существующих deprecation warnings.
Полная команда: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`.
Offline сценарий выполняется также как integration test; real-provider smoke отключён.

Итог Session 09: **485 passed, 2 skipped** (101 новых tool tests), команда
`.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`.
Offline пример выполнен; `pip check` сообщает `No broken requirements found`.
44 warnings относятся к deprecated Starlette/httpx TestClient integration и
передаче timeout в TestClient; deployed HTTP client использует настроенный timeout,
а offline TestClient deadline не моделирует (timeout tests используют injected transport).

