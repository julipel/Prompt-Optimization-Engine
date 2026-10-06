# AGENTS.md

## Роль

Ты реализуешь production-like MVP Prompt Optimization Engine.
Приоритеты: корректность, тестируемость, воспроизводимость, простая
архитектура.

## Технологии

-   Python 3.11+
-   DSPy
-   GEPA как первый optimizer adapter
-   pytest
-   Pydantic допустим для валидации внешних данных
-   Typer предпочтителен для CLI
-   FastAPI только в Phase 2

## Архитектурные правила

1.  Domain/Application не импортируют DSPy.
2.  DSPy/GEPA находится только в adapter layer.
3.  Не связывать core с конкретным LLM-провайдером.
4.  Все внешние зависимости скрывать за ports/protocols.
5.  Не добавлять БД в MVP: registry хранится в файловой системе.
6.  Не добавлять web UI, Celery, Redis, PostgreSQL, Docker orchestration
    без отдельной задачи.
7.  Не создавать «универсальный фреймворк на все случаи». Реализовать
    только требования текущей задачи.
8.  Production prompt никогда не заменяется автоматически.
9.  Critical failure имеет приоритет над aggregate score.
10. Если критерий можно проверить кодом, не использовать LLM-as-a-judge.

## Качество

-   type hints для публичных функций;
-   небольшие функции и явные зависимости;
-   meaningful exceptions;
-   deterministic unit tests;
-   внешние LLM-вызовы mock/fake в unit tests;
-   никаких реальных платных API-вызовов в обычном `pytest`.

## Definition of Done

-   код запускается;
-   тесты проходят;
-   нет TODO, скрывающих обязательную функциональность задачи;
-   публичные интерфейсы документированы;
-   ошибки обрабатываются явно;
-   новая функциональность покрыта тестами.

## Команды

Ожидается, что после bootstrap будут доступны: `pytest -q`
`python -m prompt_optimizer --help`

Если выбран lint/type-check, зафиксировать инструмент в pyproject.toml и
поддерживать его прохождение.
