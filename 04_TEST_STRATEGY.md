# 04. Test strategy

## Unit

Проверить: - dataset validation; - metric calculations; - critical
gates; - version transitions; - comparison logic; - filesystem
repository; - report serialization.

Все LLM/DSPy зависимости --- fake/mock.

## Integration

-   JSONL -\> evaluation -\> report;
-   baseline -\> fake optimizer -\> candidate -\> comparison;
-   registry lifecycle;
-   CLI commands.

## Regression

Хранить небольшой фиксированный demo dataset. Тест должен
гарантировать: - неизвестная цена не превращается в выдуманную; -
booking не подтверждается без успешного tool result; - candidate с
critical failure получает reject; - candidate с улучшением и без
regressions может получить approve.

## GEPA smoke test

Отдельный integration marker. Не должен запускаться в стандартном unit
suite, если требует API credentials. При отсутствии credentials --- skip
с понятным сообщением.

## Test data

Только синтетические данные. Никаких реальных пациентов, телефонов и
медицинских записей.
