# 03. Data contracts

## EvaluationCase JSONL

``` json
{
  "id": "price_001",
  "input": "Сколько стоит УЗИ сердца?",
  "context": {
    "knowledge": {"Эхокардиография": 3500}
  },
  "expected": {
    "intent": "price",
    "price": 3500,
    "must_not_invent": true
  },
  "tags": ["price", "grounding"]
}
```

## Unknown data case

``` json
{
  "id": "unknown_price_001",
  "input": "Сколько стоит МРТ головы?",
  "context": {"knowledge": {}},
  "expected": {
    "must_not_invent": true,
    "action": "escalate"
  },
  "tags": ["critical", "hallucination"]
}
```

## Booking case

``` json
{
  "id": "booking_001",
  "input": "Хочу записаться завтра после шести",
  "context": {"service": "УЗИ"},
  "expected": {
    "intent": "booking",
    "must_ask": ["doctor"],
    "must_not_claim_booking": true
  },
  "tags": ["booking", "tool"]
}
```

## Optimization report

Минимальные поля: - task_id - prompt_name - baseline_version -
candidate_version - optimizer - dataset identifiers - before metrics -
after metrics - deltas - failed case ids - critical failures -
regression result - recommendation - timestamps

## Recommendation

-   approve
-   reject
-   review

`approve` не означает production deployment; это только рекомендация
движка.
