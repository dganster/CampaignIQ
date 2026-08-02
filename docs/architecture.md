# CampaignIQ Architecture

## Design Philosophy

CampaignIQ stores trading facts rather than trading interpretations.

Named strategies such as Iron Condor, Calendar, Vertical, Butterfly, or Backratio are interpretations of combinations of option legs. They are not fundamental data structures.

## Architectural Principles

1. Store facts, not trading terminology.
2. Broker-specific code ends at the importer boundary.
3. A Trade is a collection of one or more instrument legs.
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

The translation boundary separates broker-specific source models from
CampaignIQ's core domain model.

On the source side are broker concepts such as orders, fills, account
activity, and statement sections.

On the domain side are CampaignIQ concepts such as Trades, Positions,
and Campaigns.

Crossing the translation boundary removes broker-specific terminology
from the core domain model.

## Modeling Principle

CampaignIQ models trading decisions rather than broker mechanics.

Broker platforms focus on how orders are executed.

CampaignIQ focuses on the logical trading decisions made by the trader.

Multiple broker fills may contribute to a single Trade.

Multiple Trades may contribute to a Position.

Multiple Positions may contribute to a Campaign.

Each layer adds meaning while preserving the facts established by the
previous layer.
