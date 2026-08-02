# CampaignIQ Architecture

## Design Philosophy

CampaignIQ stores trading facts rather than trading terminology.

Named strategies such as Iron Condor, Calendar, Vertical, Butterfly, or Backratio are interpretations of combinations of option legs. They are not fundamental data structures.

## Architectural Principles

1. Store facts, not trading terminology.
2. Broker-specific code ends at the importer boundary.
3. An order is a collection of option legs.
4. Position structures are derived from option legs and their relationships.
5. Domain objects answer questions of fact.
6. Evaluations interpret facts to support trading decisions.

## Translation Principle

CampaignIQ translates broker-specific operational records into
broker-independent domain concepts.

The translation occurs at the import boundary.

Beyond that boundary, the core domain model contains only
CampaignIQ concepts and is independent of any broker platform.

## Translation Boundary

CampaignIQ separates broker-specific operational records from the
CampaignIQ domain model.

Broker platforms record orders, fills, and account activity.

CampaignIQ translates those operational records into broker-independent
domain concepts such as Trades, Positions, and Campaigns.

This separation isolates broker-specific behavior from the core
Trading Intelligence model.

## Modeling Principle

CampaignIQ models trading decisions rather than broker mechanics.

Broker platforms focus on how orders are executed.

CampaignIQ focuses on the logical trading decisions made by the trader.

Multiple broker fills may contribute to a single Trade.

Multiple Trades may contribute to a Position.

Multiple Positions may contribute to a Campaign.

Each layer adds meaning while preserving the facts established by the
previous layer.
