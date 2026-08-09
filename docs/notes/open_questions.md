# Open Questions

## Does CampaignIQ need a Fill domain object?

Current thinking:

Probably not.

Reason:

Fills describe broker execution rather than trader intent.

Status:

Open

## Should Investing Principles become part of the domain?

Examples:

- Diversification
- Position sizing
- Risk management

Status:

Needs additional exploration.


## Investigate replacing OptionLeg with a more general Execution concept.

Status:

Open

## How should CampaignIQ represent investment intent?

Brokerage transactions provide observable execution facts, but those facts do not
necessarily reveal the investor's complete Investment Thesis.

Directional character such as bullish or bearish may sometimes be inferred from
a Trade, particularly for options, but directional character is not equivalent
to Investment Thesis.

CampaignIQ should distinguish observable transaction facts, inferred directional
character, and the investor's stated or inferred Investment Thesis.

Status:

Open
