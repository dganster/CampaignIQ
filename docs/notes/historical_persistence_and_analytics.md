# Historical Persistence and Analytical Foundation

## Status

Design decision recorded during the CampaignIQ historical-completeness checkpoint.

## Core principle

**CampaignIQ's historical database is the analytical foundation of the product.**

Monthly import is not intended to create isolated monthly reports. Each imported month extends a continuous account history that can support reconstruction, reconciliation, and later analytical capabilities.

## Persistent history

The current prototype uses in-memory domain objects and does not yet have a database or persistence layer. The product architecture should introduce a **relational database** for durable account history.

The historical store should preserve the underlying evidence and the derived interpretations separately.

### Source/evidence layer

Retain the facts reported by the source systems, including as applicable:

- imported brokerage documents and source records
- account trade history
- executions and trade legs
- option assignments/exercises and other economic events
- pending/open activity and settlement information
- broker position snapshots
- realized gain/loss records

### Derived CampaignIQ layer

Retain the results derived from the source evidence, including:

- positions and position history
- lots and cost-basis attribution
- campaign membership and campaign relationships
- realized P&L attribution
- reconciliation results and completeness status

### Analytical layer

Analytical observations should be derived from the historical record and be **recomputable**, rather than treated as immutable source facts. Examples include:

- returns
- duration
- capital employed
- annualized return
- drawdown
- win/loss statistics
- strategy attribution
- behavioral or trading-pattern measures

## Adding subsequent months

When a new month is imported, CampaignIQ should extend the existing history rather than treat the new month as an independent dataset.

Conceptually:

```text
December → January → February → March → ...
     continuous account history
```

The prior month's ending position state becomes the next month's opening context, while the new month's source documents provide additional evidence and an independent broker reconciliation point.

Historical months should not be deleted or overwritten merely because a new month is added.

## No artificial history limit

CampaignIQ should not impose an arbitrary limit such as 12, 24, or 60 months of history. The intended scope is the user's **entire CampaignIQ account history**, subject to ordinary practical storage and performance constraints.

Long history is especially important because a campaign may begin substantially earlier than the month in which the user first starts using CampaignIQ.

## Reconstructability

The system should retain enough underlying source evidence that a future improvement to CampaignIQ's reconstruction rules can recompute derived results.

For example, if campaign or option-roll logic improves six months later, CampaignIQ should be able to recalculate campaigns from the preserved historical evidence rather than being limited to previously stored campaign summaries.

This implies a strong separation between:

```text
Historical evidence
        ↓
CampaignIQ reconstruction / interpretation
        ↓
Analytical results
```

The historical evidence is the durable foundation; reconstruction and analytics are interpretations that can evolve.

## Unresolved and excluded historical cases

A campaign that cannot currently be resolved because historical evidence is missing must **not** be silently truncated or discarded.

If the user chooses to exclude an unresolved campaign because the missing data cannot be obtained, the database should retain:

- all known historical evidence
- the unresolved status/reason
- the user's exclusion decision
- enough information to identify the affected campaign later

If the user subsequently supplies the missing historical data, CampaignIQ should be able to revisit the case, resolve the campaign, and recalculate affected historical and analytical results.

Therefore:

> **No historical evidence is discarded merely because CampaignIQ cannot currently resolve it. Analytical results are derived from retained historical evidence and can be recalculated when additional evidence becomes available.**

## Why longitudinal history matters

Analytical capabilities will depend heavily on history. Examples of questions that require multi-month or multi-year data include:

- Which strategies actually make money over time?
- Which underlyings produce the best risk-adjusted returns?
- How long do campaigns typically last?
- How often do covered calls outperform simply holding the stock?
- Which campaigns generate most of the realized P&L?
- How does performance change across market environments?
- What are historical win rate, expectancy, drawdown, and capital-utilization characteristics?
- Which positions repeatedly tie up capital without producing adequate returns?
- How have campaign decisions and adjustment behavior evolved over time?

Some of these questions cannot be answered reliably from a single month's reconstruction.

## Architectural implication

Persistence should be introduced before the analytical layer becomes substantial. The monthly reconstruction engine should ultimately write/read against a durable historical store rather than becoming the database itself.

The intended conceptual architecture is:

```text
                    CAMPAIGNIQ HISTORY
                           │
         ┌─────────────────┼─────────────────┐
         ↓                 ↓                 ↓
    Transactions       Positions        Campaigns
         │                 │                 │
         └─────────────────┼─────────────────┘
                           ↓
                    Analytical Engine
                           │
         ┌─────────┬───────┼───────┬──────────┐
         ↓         ↓       ↓       ↓          ↓
        P&L      Risk    Returns  Behavior  Strategy
```

## Open implementation question

The precise relational schema, database engine, migration strategy, and repository/API boundaries remain to be designed. The decision recorded here is architectural: **CampaignIQ needs durable relational historical storage, with preserved source evidence and recomputable derived results, before the analytical product is built out.**
