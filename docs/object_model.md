# CampaignIQ Object Model

## Purpose

This document defines how the CampaignIQ domain model is represented as software objects.

The Domain Model describes business concepts.

The Object Model describes the software representation of those concepts.

The goal is to preserve the integrity of the domain while producing an implementation that is simple, extensible, and resistant to change.

---

# Design Principles

## Model Reality

Objects represent real business concepts, not implementation convenience.

Examples:

- Position History exists.
- Trade exists.
- Execution exists.
- Covered Call does not exist as a stored object.

Covered Call is an interpretation of facts.

---

## Preserve Facts

Business objects represent immutable facts.

Objects are never modified to rewrite history.

New business events are appended.

Current state is always derived from historical facts.

---

## Separate Intent from Execution

Trading intent and market execution are different concepts.

Example:

Buy 100 IBM

may execute as

40 @ 249.98
35 @ 250.01
25 @ 250.03

The business decision is one trade.

The market produced three executions.

CampaignIQ models both.

---

## Separate Facts from Analysis

Domain objects represent facts.

Analysis objects interpret facts.

Examples:

Facts

- Campaign
- Position History
- Trade
- Execution

Analysis

- Covered Call
- Wheel
- Performance
- Risk
- P&L

Analysis never modifies facts.

---

# Object Hierarchy

```
Campaign
    │
    ├── Position History
    │       │
    │       └── Position Events
    │               │
    │               ├── Trade
    │               ├── Assignment
    │               ├── Exercise
    │               ├── Expiration
    │               ├── Dividend
    │               └── Corporate Action
    │
    └── Analysis
```

---

# Core Objects

## Campaign

Represents a single investment thesis.

Responsibilities:

- owns Position Histories
- records business events
- coordinates events affecting multiple histories

Campaign is the aggregate root.

---

## Position History

Represents one continuous ownership period for a single instrument.

Begins:

- Buy
- Assignment

Ends:

- Sell
- Assignment
- Exercise
- Expiration

Responsibilities:

- maintain ordered Position Events
- reconstruct state
- calculate realized results
- determine whether ownership is open or closed

---

## Position Event

An immutable business event.

Position Events are historical facts.

Examples:

- Trade
- Assignment
- Exercise
- Expiration
- Dividend
- Corporate Action

Position Events know what happened.

They do not know how Campaign organizes history.

---

## Trade

A completed trading transaction.

A Trade is a Position Event.

A Trade consists of one or more Legs.

---

## Leg

Represents one component of a Trade.

Examples:

Buy 100 IBM

Sell 1 IBM Aug 250 Call

Responsibilities:

- identify Instrument
- identify Side
- identify Quantity

A Leg owns one or more Executions.

A Leg does not contain execution prices or timestamps.

---

## Execution

Represents one market fill.

Responsibilities:

- execution quantity
- execution price
- execution timestamp
- commission (optional)
- execution identifier (optional)

Executions are immutable.

Multiple Executions may satisfy one Leg.

---

## Instrument

Represents a tradable financial instrument.

Instrument is an abstraction.

Examples:

- Equity
- Option Contract

Future instrument types may be added without changing Trade or Leg.

---

# Ownership

Campaign
    owns Position Histories

Position History
    owns Position Events

Trade
    owns Legs

Leg
    owns Executions

Objects own only those objects whose lifecycle they control.

---

# Invariants

## Campaign

- owns one or more Position Histories
- records immutable facts

---

## Position History

- represents one continuous ownership period
- events are ordered
- history is immutable

---

## Position Event

- immutable
- timestamped
- historical fact

---

## Trade

- contains one or more Legs

---

## Leg

- references exactly one Instrument
- has one Side
- has one Quantity
- has one or more Executions

---

## Execution

- immutable
- belongs to exactly one Leg

---

## Instrument

- uniquely identifies a tradable security

---

# Model Evolution

Original design

Trade
    └── Leg

Current design

Trade
    └── Leg
            └── Execution

Reason:

Execution is a separate business concept.

Separating Leg from Execution allows CampaignIQ to model brokers that produce partial fills while preserving a simple representation for brokers that do not.

---

# Future Objects

The following objects are expected to be introduced later.

- Money
- Quantity
- Portfolio
- Cash Ledger
- Campaign Analysis
- Strategy Detector
- Performance Analyzer
- Risk Analyzer

These are intentionally deferred until the core object model is stable.
