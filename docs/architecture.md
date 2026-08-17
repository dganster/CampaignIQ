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

## Source Modeling Principle

CampaignIQ source models preserve the structure of broker data
without interpreting its meaning.

Source models represent broker concepts such as statements,
sections, and rows.

Interpretation begins in the importer layer, where broker data is
translated into CampaignIQ domain concepts.


## Current Economic Data Pipeline

CampaignIQ currently processes trading data through the following
conceptual pipeline:

Broker source records
→ source models
→ domain Trades and PositionEvents
→ Campaign reconstruction
→ Position and LotBook state
→ LotAllocations
→ RealizedAttribution
→ Campaign realized P&L

Trades represent completed trading activity. PositionEvents represent
non-trade economic events that affect positions, including assignments,
expirations, exercises, and other position changes.

Campaign reconstruction groups Trades into investment Campaigns.
PositionHistory preserves the chronological economic history of Trades
and PositionEvents.

The LotBook maintains opening lots and closing-lot allocations. Campaign
provenance may be assigned to opening lots when the available historical
data permits an unambiguous determination.

RealizedAttribution reconciles broker-reported realized gain/loss
records with CampaignIQ closing activity and opening-lot ancestry.

### Broker-Reported Basis

CampaignIQ treats broker-reported realized cost basis as the authoritative
basis for realized gain/loss reconciliation.

CampaignIQ's LotBook establishes lot availability and provenance. It
does not replace the broker's basis calculation.

This distinction allows CampaignIQ to preserve both the broker's
financial facts and CampaignIQ's independent understanding of the
investment campaign that produced those facts.

### Deferred Review Items

The following areas have been identified for future correctness review:

- Assignment-date selection in `RealizedLotAttributor`.
- Handling of multiple economic events for the same instrument when
  determining whether trade closing activity should suppress event-based
  attribution.

These items are intentionally deferred until concrete historical cases
or tests demonstrate that refinement is necessary.

