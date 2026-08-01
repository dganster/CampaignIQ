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
