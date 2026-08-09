# Campaign Reconstruction Rules

## Purpose

Campaign Reconstruction transforms brokerage activity into Campaigns.

Its purpose is to reconstruct the investor's historical pursuit of Investment Theses by recognizing patterns in trading activity.

Campaign Reconstruction is the core intellectual property of CampaignIQ.

Campaign Reconstruction does not attempt to recreate brokerage positions or account history. Those already exist.

Instead, it reconstructs the continuity of the investor's Investment Theses.

## Campaign Assignment

Every imported Trade belongs to exactly one Campaign.

A Campaign may be established in either of two ways:

- **Explicit:** The investor identifies the Campaign and assigns trades to it.
- **Inferred:** CampaignIQ determines that trades are likely part of the same Campaign based on available evidence.

Explicit campaign assignments take precedence over inferred relationships.

## Campaign Continuity

A new Campaign begins when the available evidence indicates that the investor has adopted a new Investment Thesis.

A Campaign continues while the investor continues pursuing the same Investment Thesis, regardless of changes in trading strategy, positions, assignments, or exercises.

Closing a position does not necessarily end a Campaign.

A Campaign ends when its Investment Thesis is completed or abandoned, which may or may not coincide with closing the final position.

## Inference

CampaignIQ may use observable trading characteristics as evidence when inferring Campaign continuity.

These characteristics may include:

- underlying security
- timing
- position effects
- changes in contracts
- changes in trading strategy
- other available brokerage information

Inference rules are evidence-based heuristics. They do not define what a Campaign is.

When Campaign continuity cannot be determined with sufficient confidence, CampaignIQ should request guidance from the investor.

CampaignIQ should preserve the evidence supporting an inferred Campaign and allow the investor to accept, modify, or reject the inference.

## Trader Progression

CampaignIQ should support investors at different levels of investment thinking.

### One-Off Trader

CampaignIQ should not require investors to define Campaigns.

Instead, it should infer possible relationships among trades and present those relationships as inferred Campaigns.

The purpose is not only organization, but to help investors recognize the concept of an investment Campaign.

### Campaign-Oriented Trader

Investors who already think in terms of Campaigns should be able to explicitly define Campaigns and associate trades with them.

### Portfolio-Oriented Trader

CampaignIQ should eventually support portfolio-level analysis of Campaigns, exposures, risk, allocation, and related considerations.

Portfolio management is a layer above Campaigns rather than a replacement for them.
