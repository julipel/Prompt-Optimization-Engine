# Session 05 --- End-to-end optimization pipeline

## Goal

Связать baseline, optimizer, candidate evaluation и regression decision.

## Flow

baseline validation evaluation -\> optimize on train -\> candidate
validation evaluation -\> compare -\> gates -\> recommendation.

## Implement

-   OptimizePrompt use case;
-   CompareVersions;
-   configurable thresholds;
-   hard constraints;
-   structured result.

## Rules

Critical failure =\> reject независимо от aggregate score. Нельзя
оценивать улучшение только на train dataset.

## Tests

improvement, degradation, equal result, critical regression, threshold
regression, optimizer failure.

## Acceptance

Fake optimizer позволяет полностью прогнать pipeline без сети.
