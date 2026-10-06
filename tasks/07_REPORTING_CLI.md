# Session 07 --- Reports + CLI

## Goal

Получить полноценный MVP для локального использования.

## CLI

Минимум: - `prompt-opt evaluate` - `prompt-opt optimize` -
`prompt-opt versions` - `prompt-opt approve` - `prompt-opt reject` -
`prompt-opt promote`

## Output

Краткий terminal summary + JSON report file.

Показывать before/after/delta, failed cases, critical failures,
recommendation.

## Tests

CLI через test runner, exit codes, invalid args, report schema.

## Acceptance

Пользователь может выполнить полный цикл из CLI без ручного изменения
файлов registry.
