# CampaignIQ Domain Glossary

## Purpose

This glossary defines the ubiquitous language of CampaignIQ.

Every term in this glossary has one clear, unambiguous meaning.
If a commonly used industry term has multiple meanings, CampaignIQ
prefers a more precise term.

The goal is for the code to read the same way experienced traders
think and speak.

## Design Principles

CampaignIQ intentionally avoids overloaded terminology.

For example, the word *Strategy* is commonly used to describe:

- an investing philosophy,
- a trading methodology,
- an option position structure,
- a risk management approach.

Because these meanings are different, CampaignIQ does not use
*Strategy* as a core domain concept.

Instead, it prefers precise terms such as:

- Trading Methodology
- Position Structure
- Campaign

When an industry term has multiple accepted meanings, CampaignIQ
chooses a more precise term with a single definition.

## Instrument

A tradable financial asset.

Examples include:

- Equity
- Exchange-Traded Fund (ETF)
- Futures Contract
- Forex Currency Pair
- Cryptocurrency

## Trade

A Trade is the successful execution of a single trading order.

A Trade represents one logical trading decision made by the trader.

A Trade may consist of one or more legs and may be executed through one
or more broker fills.

Trades are the building blocks from which Positions are derived.

Trades are the building blocks from which Positions are derived.

## Fill

A Fill is a broker execution that satisfies all or part of a trading
order.

A single Trade may require one or more broker fills.

Fills are operational details produced by the broker and are preserved
during import but are not part of CampaignIQ's core trading model.



Notes:

- An option is not an Instrument.
- An option is a derivative contract whose underlying is an Instrument.


