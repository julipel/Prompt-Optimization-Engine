# Подключение к Claude Code и Claude Desktop

Оптимизатор работает как отдельный сервис, а агенты подключаются к нему через
MCP. Агент может только запросить оптимизацию и прочитать отчёт. Одобрение и
вывод в production остаются за человеком.

```
Claude Code / Desktop ──MCP (stdio)──> prompt-opt-mcp ──HTTP──> сервис оптимизатора
   + skill с правилами                 тонкий клиент:          FastAPI, GEPA + реальная модель,
                                       датасеты по id,         реестр промптов и отчёты на диске,
                                       без ключей и DSPy       ключ API только здесь
```

| Компонент | Где | Что делает |
| --- | --- | --- |
| Сервис | `prompt_optimizer.http_runtime:create_real_app` | Оптимизация, оценка, реестр, отчёты |
| MCP-сервер | `prompt-opt-mcp` (`interfaces/mcp_server.py`) | Публикует `optimize_prompt` и `get_optimization_report` |
| Skill | `integrations/claude/skills/prompt-optimizer/` | Правила для Claude: датасеты, чтение результата, запрет approve/promote |

Один сервис обслуживает любые проекты: промпты различаются по `prompt_name`
в общем реестре.

## 1. Установка

```bash
pip install -e ".[mcp]"        # или ".[dev]" — включает mcp и pytest
```

## 2. Запуск сервиса

```bash
set -a; source .env; set +a                     # OPENAI_API_KEY, GEPA_MODEL, GEPA_REFLECTION_MODEL
export PROMPT_REGISTRY_ROOT=~/prompt-optimizer/registry
uvicorn --factory prompt_optimizer.http_runtime:create_real_app --host 127.0.0.1 --port 8765
```

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `PROMPT_REGISTRY_ROOT` | — (обязательна) | Реестр версий промптов |
| `PROMPT_REPORTS_ROOT` | `<registry>-reports` | Отчёты; сохраняются после перезапуска |
| `GEPA_MODEL` / `GEPA_REFLECTION_MODEL` | — (обязательны) | Модель ответа и модель рефлексии, `provider/model` |
| `PROMPT_OPT_CREDENTIAL_ENV` | `OPENAI_API_KEY` | Имя переменной с ключом; без ключа сервис не стартует |
| `GEPA_MAX_TOKENS` | `16000` | Для reasoning-моделей OpenAI нужно ≥16000 |
| `GEPA_MAX_METRIC_CALLS` | `150` | Бюджет GEPA на один запуск (≈8 минут, сотни платных вызовов) |
| `PROMPT_OUTPUT_DESCRIPTION` | нейтральное описание JSON | Подсказка к выходному полю сигнатуры DSPy |

Запускайте сервис только на `127.0.0.1`: аутентификации у него нет, а
`/prompts/.../promote` доступен любому, кто достучится до порта.

Без платных вызовов: `uvicorn --factory prompt_optimizer.http_runtime:create_demo_app`
(fake-ответы, см. README).

## 3. Конфигурация MCP-сервера

Скопируйте [`integrations/claude/mcp.example.json`](../integrations/claude/mcp.example.json)
в `~/.config/prompt-optimizer/mcp.json` и пропишите свои датасеты:

```json
{
  "base_url": "http://127.0.0.1:8765",
  "timeout_seconds": 1800,
  "datasets": {
    "support-tickets@1": {"train": "/data/tickets/train.jsonl", "validation": "/data/tickets/validation.jsonl"}
  }
}
```

Относительные пути считаются от файла конфигурации. Агент передаёт только
`dataset_id` и `dataset_version`. `timeout_seconds` должен покрывать весь
прогон GEPA. При обрыве результат неизвестен, и повторять запуск нельзя,
пока вы не проверили реестр.

## 4. Подключение к Claude Code

```bash
claude mcp add --scope user prompt-optimizer -- \
  ~/Prompt-Optimization-Engine/.venv/bin/prompt-opt-mcp --config ~/.config/prompt-optimizer/mcp.json
ln -s ~/Prompt-Optimization-Engine/integrations/claude/skills/prompt-optimizer ~/.claude/skills/prompt-optimizer
```

`--scope user` и skill в `~/.claude/skills/` делают инструменты доступными во
всех проектах. Проверка: `claude mcp list`, затем в сессии `/mcp`.

## 5. Подключение к Claude Desktop

В `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "prompt-optimizer": {
      "command": "/home/<user>/Prompt-Optimization-Engine/.venv/bin/prompt-opt-mcp",
      "args": ["--config", "/home/<user>/.config/prompt-optimizer/mcp.json"]
    }
  }
}
```

Если Claude Desktop запущен в Windows, а проект лежит в WSL, запускайте
MCP-сервер через WSL. Сервис на `127.0.0.1` внутри WSL доступен MCP-серверу,
потому что оба работают в одной WSL-среде:

```json
{
  "mcpServers": {
    "prompt-optimizer": {
      "command": "wsl.exe",
      "args": ["-e", "/home/<user>/Prompt-Optimization-Engine/.venv/bin/prompt-opt-mcp",
               "--config", "/home/<user>/.config/prompt-optimizer/mcp.json"]
    }
  }
}
```

## 6. Работа из другого проекта

1. Подготовьте датасет по правилам из skill: JSONL, точные значения полей,
   train/validation/test без пересечений. Добавьте его в `mcp.json`.
2. Положите исходный промпт в реестр (это делает человек):
   `prompt-opt bootstrap --registry-root $PROMPT_REGISTRY_ROOT --prompt-name tickets --prompt-version v001 --prompt-file prompt.txt`
3. Попросите Claude: «оптимизируй промпт tickets на датасете support-tickets@1».
   Claude подтвердит запуск, вызовет `optimize_prompt` и разберёт отчёт.
4. Решение примите сами: `prompt-opt approve` и `prompt-opt promote`. Текст
   новой production-версии берётся из реестра (`GET /prompts/tickets/production`
   или `prompt-opt versions`), и вы переносите его в свой проект.

## Ограничения

- Только задачи со структурным JSON-ответом. Свободный текст требует
  LLM-as-a-judge, а движок его намеренно не использует.
- Запрос синхронный: соединение держится весь прогон GEPA. Очереди задач нет.
- Один набор моделей и один бюджет на сервис. Для другой модели нужен второй
  экземпляр сервиса на другом порту.
- Финальную оценку на test-сплите инструмент не делает. Её запускает человек
  через HTTP `POST /evaluations` реального сервиса: CLI `prompt-opt evaluate`
  поддерживает только записанные (fake) ответы.
