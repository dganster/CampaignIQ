# Lot and Basis Attribution

## Purpose

CampaignIQ should explain a closed transaction by identifying the opening position lot(s) it consumed and by preserving the broker's authoritative cost basis.

## Principles

- The lot book tracks signed long and short lots in FIFO order as a **position ancestry model**.
- Pre-period lots can be seeded from an authoritative broker position snapshot.
- A closing trade is allocated to available lots; if no sufficient lot exists, the condition is an ancestry/data-completeness problem, not an invitation to invent a lot.
- Schwab's Realized Gain/Loss report is authoritative for the **reported closed cost basis**. CampaignIQ should not silently replace Schwab's basis with a home-grown tax-lot calculation.
- CampaignIQ may use its FIFO lot book to identify candidate ancestry and then attach the broker-reported basis to the matched closure.
- When one broker record consumes multiple CampaignIQ lots, broker total basis may be allocated across the matched quantities for attribution, with the broker total remaining authoritative.
- Broker-reported proceeds and gain/loss are preserved as source facts and can be reconciled independently.
- A later implementation can compare CampaignIQ's lot selection with Schwab's reported FIFO result; a mismatch should be reported explicitly rather than silently corrected.

## Current implementation checkpoint

The domain now contains:

- `Lot`
- `LotAllocation`
- `LotBook`
- `RealizedGainLossRecord`
- `RealizedAttribution`
- `RealizedLotAttributor`

The implementation supports long and short lots, partial closes, seeded pre-period lots, FIFO allocation, and attachment of Schwab-reported basis.

The next step is to connect the actual Schwab Realized Gain/Loss importer and January monthly reconstruction to this domain so the 28 January 2026 broker records can be attributed to concrete CampaignIQ lots and campaigns.


## Checkpoint: broker aggregation of closing activity

Schwab's Realized Gain/Loss report may aggregate multiple executions into one
record, while Thinkorswim Account Trade History may contain several fills.
Attribution therefore matches by closed date, instrument, and quantity across
multiple closing activities rather than requiring one-to-one trade records. It
also supports consuming part of an activity when one execution must be split
across multiple broker records.

The January 2026 data contains real examples: NVDA is reported as one 5-contract
realized record while the trade history has 4 + 1 closing fills; VRT, UNH, ORCL,
LIN, and other transactions similarly require aggregation or split handling.

The remaining January integration issue is assignment timing: Schwab's DXCM and
EL realized records use the January 9 assignment date, while the corresponding
stock transaction appears later in the account transaction history. Assignment
events therefore need to be treated as economic closure evidence rather than
matched solely to the later settlement trade.

## Checkpoint: assignment-date economic closure

Schwab may date a realized stock disposition on the option-assignment date even
when the corresponding stock transaction appears later in Account Trade
History. CampaignIQ therefore preserves the actual stock trade as the lot
consumption evidence while allowing an assignment event to supply the
broker's economic closure date for realized-record matching.

The assignment's underlying stock change is not consumed a second time by the
lot attributor when a corresponding explicit stock closing trade exists. This
prevents double-counting the same economic disposition. The option-side
assignment change remains an economic position event.
