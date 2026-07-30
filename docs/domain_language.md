# CampaignIQ Domain Language

## Purpose

CampaignIQ is built upon a shared domain language.

This document defines the core business concepts used throughout the system. These definitions establish a common vocabulary for developers, users, documentation, and future design discussions.

Unless there is a compelling reason otherwise, these terms should be used consistently throughout the project.

---

## Investment Thesis

An **Investment Thesis** is the rationale for committing capital.

It represents the investor's expectations, objectives, assumptions, and risk tolerance at the time capital is committed.

A single Investment Thesis may give rise to one or more Campaigns.

---

## Campaign

A **Campaign** is the complete lifecycle of capital committed in pursuit of a single investment objective.

A Campaign begins when capital is first committed and ends when that capital has been fully withdrawn or the investment objective has been abandoned.

A Campaign may contain one or more Positions.

Campaigns are the primary unit of analysis within CampaignIQ.

---

## Position

A **Position** represents ownership of a specific financial instrument.

Examples include:

- 100 shares of IBM
- 1 short IBM Aug 250 Call
- 2 ES futures contracts

Positions change over time as Position Events occur.

A Position has a history.

---

## Position History

**Position History** is the chronological record of changes to a Position.

It is reconstructed from Position Events.

Position History allows CampaignIQ to determine what was owned, when it was owned, and how ownership evolved over time.

---

## Position Event

A **Position Event** is a business event that creates, modifies, or closes one or more Positions.

Position Events include investor-initiated actions, broker actions, exchange actions, and corporate actions.

Examples include:

- Trade
- Assignment
- Exercise
- Expiration
- Dividend
- Stock Split
- Merger
- Spin-off

Position Events are immutable historical facts from which Position History is reconstructed.

---

## Trade

A **Trade** s a Position Event resulting from an order executed in the marketplace.

Examples include:

- Buy to Open
- Sell to Open
- Buy to Close
- Sell to Close
- Buy Stock
- Sell Stock

A Trade consists of one or more Legs.

---

## Leg

A **Leg** is one component of a Trade.

Each Leg affects exactly one Position.

Examples include:

- Buy 100 IBM
- Sell 1 IBM Call
- Buy 2 ES Futures

Multi-leg option strategies consist of multiple Legs executed as a single Trade.

---

## Execution

An **Execution** is the actual market fill reported by the broker.

One Leg may consist of multiple Executions.

Executions are immutable historical facts.

---

## Capital

**Capital** represents the financial resources committed to a Campaign.

CampaignIQ measures both the amount of capital committed and how efficiently that capital was employed over time.

---

## Decision

A **Decision** is an action taken by the investor that changes the state of a Campaign.

Trades are evidence of Decisions.

CampaignIQ analyzes Decisions rather than recommending them.

---

## Outcome

An **Outcome** is the measurable result of one or more Decisions.

Outcomes include:

- Realized profit or loss
- Unrealized gain or loss
- Duration
- Capital utilization
- Assignment
- Expiration

CampaignIQ analyzes Outcomes to improve understanding of prior Decisions.

---

## Relationship Summary

```text
Investment Thesis
        │
        ▼
    Campaign
        │
        ▼
     Position
        │
        ▼
 Position Event
        │
        ├──────────────┐
        ▼              ▼
     Trade      Assignment
        │        Exercise
        ▼        Expiration
       Leg       Dividend
        │        Stock Split
        ▼        Merger
   Execution     Spin-off
```
