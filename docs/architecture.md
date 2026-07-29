# CampaignIQ Architecture

## Design Philosophy

CampaignIQ stores trading facts rather than trading terminology.

Named strategies such as Iron Condor, Calendar, Vertical, Butterfly, or Backratio are interpretations of combinations of option legs. They are not fundamental data structures.

## Architecture Principles

1. Store facts, not strategy names.
2. Broker-specific code ends at the importer boundary.
3. An order is a collection of option legs.
4. Strategy names are derived from option legs and their relationships.
