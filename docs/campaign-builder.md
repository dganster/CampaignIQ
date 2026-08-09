# Campaign Builder

The Campaign Builder reconstructs investment campaigns from brokerage transactions.

Its purpose is to transform chronological trades into meaningful campaigns that reflect the investor's decisions and investment thesis.

## Rule 1

An opening trade creates a new campaign unless it clearly extends an existing one.

## Rule 2

Trades that modify an existing position belong to the active campaign.

## Rule 3

A campaign ends when its investment thesis is completed or abandoned, which may or may not coincide with closing the final position.

## Trader Progression

CampaignIQ should support three levels of investor thinking.

### One-Off Trader

Many retail investors think primarily in terms of individual trades.

CampaignIQ should not require these investors to define campaigns.

Instead, CampaignIQ should infer possible relationships among their trades and present those relationships as inferred campaigns.

The purpose is not only organization, but to help the investor recognize the concept of an investment campaign.

### Campaign-Oriented Trader

More sophisticated investors already think in terms of campaigns.

CampaignIQ should allow these investors to explicitly define campaigns and associate trades with them.

Explicit campaign definitions should take precedence over inferred relationships.

### Portfolio-Oriented Trader

Elite investors think in terms of portfolio management rather than individual trades or campaigns.

CampaignIQ should eventually support portfolio-level analysis of campaigns, exposures, risk, allocation, and related considerations.

Portfolio management is a layer above campaigns rather than a replacement for them.

## Inferred and Explicit Campaigns

A campaign may originate in either of two ways:

- **Inferred:** CampaignIQ determines that multiple trades are likely part of the same campaign.
- **Explicit:** The investor identifies and defines the campaign.

For inferred campaigns, CampaignIQ should preserve the evidence and reasoning supporting the inferred relationship and allow the investor to accept, modify, or reject the inference.

Campaign inference should not be presented as certainty when the available evidence is ambiguous.

The distinction between inferred and explicit campaigns allows CampaignIQ to serve investors who do not yet think in campaigns while also supporting investors who already do.
