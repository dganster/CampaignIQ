# CampaignIQ Import Pipeline

## Purpose

Broker platforms record operational trading activity.

CampaignIQ transforms that operational data into Trading Intelligence.

The import pipeline progressively enriches the data while preserving
the underlying trading facts.

## Pipeline

Broker Statement
        │
        ▼
Source Reader
        │
        ▼
Trades
        │
        ▼
Positions
        │
        ▼
Campaigns
        │
        ▼
Trading Intelligence

The Source Reader translates broker-specific records into CampaignIQ
Trades.

Trades are broker-independent and become the foundation for all
subsequent analysis.

Each stage adds meaning without altering the facts established by the
previous stage.

## Responsibilities

### Broker Statement

Represents the broker's view of trading activity.

Contains broker-specific records such as:

- executions
- orders
- dividends
- fees
- cash activity
- corporate actions

### Trading Events

Represents immutable facts extracted from the broker statement.

Trading Events are independent of any broker platform and form the
foundation of the CampaignIQ domain model.

### Trades

Represent the trader's logical intent.

Multiple broker executions or fills may contribute to a single Trade.

Examples include:

- Covered Call
- Vertical Spread
- Calendar Roll
- Stock Purchase

### Positions

Represent the trader's current or historical exposure in an instrument.

Positions are derived from Trades.

### Campaigns

Represent a continuous trading objective.

Campaigns are derived from Positions and summarize an entire trading effort.

### Trading Intelligence

Represents analyses, metrics, visualizations, and insights derived from
completed trading activity.

Trading Intelligence helps traders learn from historical performance
rather than execute trades.
