# Domain Model

## Purpose

This document defines the conceptual domain model for CampaignIQ.

The domain model describes the core business entities, their responsibilities, and their relationships. It is independent of programming language, persistence technology, and user interface.

The domain model is intended to preserve immutable investment facts while providing a foundation for decision analysis.

---

# Design Principles

The CampaignIQ domain model is guided by the following principles.

## Preserve Facts. Derive Knowledge.

Facts are never altered.

Knowledge is derived from facts.

Current positions, performance metrics, strategies, and decision analysis are all projections of historical facts.

---

## History Is the Source of Truth

The complete history of an investment is more important than its current state.

Current state can always be reconstructed from historical events.

Historical events cannot be reconstructed from current state.

---

## Position Events Are Immutable

Every change to an investment is represented as a Position Event.

Position Events are never modified after they are recorded.

Corrections are represented by additional events rather than editing historical events.

---

## Position History Represents Continuous Ownership

A Position History represents one continuous period of ownership of a financial instrument.

When ownership ends, the Position History is complete.

If the same instrument is purchased again later, a new Position History begins.

---

## Strategies Are Interpretations

CampaignIQ records investment facts, not investment strategies.

Strategies such as Covered Call, Wheel, Collar, or Iron Condor are analytical interpretations derived from Position Histories.

The same historical facts may support multiple analytical interpretations.

---

# Domain Model

```
Investment Thesis
        │
        ▼
    Campaign
        │
        ├── Position History
        │        │
        │        └── Position Events
        │                 ├── Trade
        │                 ├── Assignment
        │                 ├── Exercise
        │                 ├── Expiration
        │                 ├── Dividend
        │                 └── Corporate Action
        │
        └── Analysis
```

---

# Core Domain Objects

## Investment Thesis

An Investment Thesis represents the original investment objective and rationale.

A Campaign exists to execute an Investment Thesis.

---

## Campaign

A Campaign represents the complete lifecycle of an investment objective.

A Campaign owns one or more Position Histories.

A Campaign coordinates Position Events that affect multiple Position Histories.

A Campaign is responsible for campaign-level analysis, performance, and decision evaluation.

---

## Position History

A Position History represents one continuous ownership period of a financial instrument.

A Position History owns an ordered sequence of immutable Position Events.

A Position History is the authoritative source for reconstructing the state of an investment during that ownership period.

A Position History ends when ownership of the instrument ends.

Purchasing the same instrument again creates a new Position History.

---

## Position Event

A Position Event represents an immutable business event affecting one or more Position Histories.

Examples include:

- Trade
- Assignment
- Exercise
- Expiration
- Dividend
- Stock Split
- Merger
- Spin-off

Position Events are recorded permanently.

---

## Trade

A Trade is a Position Event resulting from one market order.

A Trade contains one or more Legs.

---

## Leg

A Leg represents one financial instrument within a Trade.

Complex option strategies may contain multiple Legs.

---

## Execution

An Execution represents one broker fill for a Leg.

Multiple Executions may satisfy a single Leg.

---

# Derived Concepts

CampaignIQ distinguishes between facts and interpretation.

## Facts

- Position Events
- Trades
- Assignments
- Exercises
- Expirations
- Dividends
- Corporate Actions

↓

## Derived State

- Current Position
- Historical Position
- Cost Basis
- Cash Flow
- Realized Profit/Loss
- Unrealized Profit/Loss

↓

## Analysis

- Covered Call
- Wheel
- Collar
- Iron Condor
- Rolling
- Decision Quality
- Risk Analysis
- Performance Analysis

Only facts are permanently stored.

State and analysis are derived from those facts.

---

# Model Evolution

## Why Position History instead of Position?

A Position exists only while an instrument is owned.

CampaignIQ must preserve ownership history after the position is closed.

Therefore, Position History is the persistent business entity.

Current Position is a derived projection of Position History.

---

## Why are strategies not part of the domain model?

Strategies describe how an investor interprets a sequence of events.

They are not business facts.

Separating facts from interpretation allows CampaignIQ to remain objective while supporting multiple analytical perspectives.

---

## Why are Position Events immutable?

Immutable history provides:

- Complete auditability
- Reproducible analysis
- Historical reconstruction
- Reliable decision analysis

Historical facts are never edited.

Corrections are represented by additional Position Events.

---

# Open Questions

The following design questions remain under consideration.

- Should PositionHistory be implemented as an Aggregate Root?
- How should cash balances be modeled?
- How should dividends affect Position History?
- How should complex corporate actions involving multiple instruments be represented?
- What relationships should exist between Position Histories participating in the same options strategy?
