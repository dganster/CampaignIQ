# CampaignIQ Domain Model — Version 1.0

## Purpose

CampaignIQ models investing as investors think about it rather than as brokerage firms record it.

Brokerage firms organize investing into accounts, positions, and transactions.

CampaignIQ organizes an investor's activity into campaigns.

The purpose of the CampaignIQ Domain Model is to define the language of investing as understood by CampaignIQ. It establishes the concepts, relationships, and rules that govern the system and serves as the foundation for every software design decision.

CampaignIQ is a knowledge system for investors. Its purpose is to preserve an investor's thinking, decisions, and experience so that every completed campaign contributes to becoming a better investor.

---

# Core Principles

## Campaigns are the primary organizing concept.

Every investment belongs to a Campaign.

Trades, positions, cash transactions, dividends, assignments, exercises, and adjustments all occur within the context of a Campaign.

CampaignIQ never views a Trade in isolation.

---

## Every Campaign begins with an Investment Thesis.

Every Campaign starts with a reason for committing capital.

The Investment Thesis defines what the investor believes, why they believe it, and what they hope to accomplish.

---

## Investment Theses are immutable.

An Investment Thesis represents the investor's thinking at a specific point in time.

It is never modified.

If the investor's thinking changes, CampaignIQ records a Thesis Revision while preserving every previous version.

History is never rewritten.

---

## CampaignIQ models investor decisions.

Brokerage firms record transactions.

CampaignIQ models the decisions that produced those transactions.

Trades are evidence that Campaign Decisions were executed.

---

## Investor knowledge is irreplaceable.

Brokerage transactions can be imported.

Market data can be downloaded.

Derived values can be recalculated.

The investor's thinking cannot.

CampaignIQ exists to preserve the investor's investment theses, decisions, notes, reflections, and lessons learned as a permanent record of the investor's growth.

---

## Every completed Campaign should increase investor knowledge.

CampaignIQ exists to help investors become better investors.

Every completed Campaign should contribute to the investor's accumulated experience, judgment, and understanding.

---

# Domain Objects

## Investor Profile

### Definition

An Investor Profile describes the investor's philosophy, objectives, preferences, constraints, experience, and risk characteristics.

The Investor Profile provides the long-term context within which Investment Theses are formed and Campaigns are undertaken.

### Characteristics

An Investor Profile evolves throughout an investor's lifetime.

Changes are preserved historically rather than replacing prior versions.

CampaignIQ uses the Investor Profile to understand how an investor's philosophy and investing behavior evolve over time.

### Typical Contents

An Investor Profile may include:

- Investing Philosophy
- Investment Objectives
- Risk Tolerance
- Risk Capacity
- Time Horizon
- Income Requirements
- Capital Preservation Requirements
- Growth Objectives
- Liquidity Requirements
- Investment Experience
- Strategy Experience
- Tax Considerations
- Investment Constraints
- Strategy Preferences

---

## Investment Thesis

### Definition

An Investment Thesis is a reasoned belief about the future behavior of an underlying security or market.

It answers the questions:

- What do I believe?
- Why do I believe it?
- How long do I expect this thesis to remain valid?

An Investment Thesis becomes part of the permanent historical record of a Campaign.

### Characteristics

An Investment Thesis is immutable.

Changes in thinking are recorded as Thesis Revisions rather than modifications to the original thesis.

---

## Campaign

### Definition

A Campaign is the complete lifecycle of implementing a single Investment Thesis.

A Campaign begins when an investor commits capital in support of an Investment Thesis.

A Campaign concludes when its Investment Thesis has been completed, abandoned, or replaced by a new Investment Thesis.

A Campaign is the primary organizational object within CampaignIQ.

### Campaign Identity

A Campaign is the persistent pursuit of a single Investment Thesis.

Campaign identity is determined by the continuity of the Investment Thesis rather than by the underlying security, trading strategy, or brokerage position.

Multiple positions, strategies, and trading decisions may occur within a single Campaign as long as they continue to implement the same Investment Thesis.

A new Campaign begins only when a new Investment Thesis is adopted.

### Contains

A Campaign contains:

- Investment Thesis
- Thesis Revisions
- Campaign Strategy
- Campaign Decisions
- Investor Notes
- Campaign Outcome
- Reflection

---

## Campaign Strategy

### Definition

A Campaign Strategy describes how an investor chooses to implement an Investment Thesis.

Multiple Campaign Strategies may be appropriate for the same Investment Thesis.

A Campaign Strategy may evolve during the lifetime of a Campaign while the Investment Thesis remains unchanged.

### Examples

- Long Stock
- Covered Call
- Cash-Secured Put
- Bull Put Vertical
- Bear Call Vertical
- Long Call
- Long Put
- Iron Condor
- Wheel

### Purpose

CampaignIQ uses Campaign Strategies to classify Campaigns, compare similar Campaigns, and help investors understand which strategies have historically been most effective for particular Investment Theses.

---

## Campaign Decision

### Definition

A Campaign Decision is an intentional action taken by the investor to advance, modify, or conclude a Campaign.

Campaign Decisions capture the investor's reasoning and intent.

One Campaign Decision may result in one or more Trades.

### Examples

- Initiate Campaign
- Increase Exposure
- Reduce Exposure
- Generate Income
- Roll Position
- Hedge Risk
- Take Profits
- Exit Campaign
- Abandon Thesis

---

## Trade

### Definition

A Trade is an executed brokerage transaction that implements a Campaign Decision.

Trades are objective historical facts recorded exactly as executed by the brokerage.

Trades may open, modify, or close Positions.

Every Trade belongs to exactly one Campaign.

---

## Position

### Definition

A Position represents the investor's current ownership or contractual interest in a financial instrument.

Positions are derived from the history of executed Trades within a Campaign.

Positions represent current state rather than permanent historical facts.

---

## Cash Transaction

### Definition

A Cash Transaction records the movement of cash into or out of a Campaign.

Examples include:

- Premium Received
- Premium Paid
- Dividends
- Interest
- Commissions
- Fees
- Assignment
- Exercise

Cash Transactions provide the financial history of a Campaign independent of its Positions.

---

## Security

### Definition

A Security is the underlying financial instrument that is the subject of an Investment Thesis.

Examples include:

- Common Stock
- ETF
- Mutual Fund
- Index
- Future
- Currency

A Campaign normally references a single underlying Security.

---

## Investor Note

### Definition

An Investor Note is a dated observation recorded during the lifetime of a Campaign.

Notes capture information that cannot be inferred from brokerage transactions.

Examples include:

- Research findings
- Changes in market outlook
- Emotional reactions
- Strategy adjustments
- Earnings observations

Investor Notes become part of the permanent history of a Campaign.

---

## Campaign Outcome

### Definition

A Campaign Outcome records the objective results of a completed Campaign.

Examples include:

- Total Return
- Income Generated
- Capital Appreciation
- Duration
- Maximum Drawdown
- Capital Committed
- Assignment Activity
- Tax Consequences

Campaign Outcomes describe what happened.

---

## Reflection

### Definition

A Reflection is the investor's evaluation of a completed Campaign.

A Reflection compares the original Investment Thesis with the Campaign Outcome.

Typical questions include:

- Was my thesis correct?
- Was my execution disciplined?
- What surprised me?
- What would I do differently next time?

Reflections explain why the Campaign produced its outcome.

---

## Lesson Learned

### Definition

A Lesson Learned is durable investing knowledge derived from one or more completed Campaigns.

Campaigns generate Lessons Learned.

Investors own Lessons Learned.

Lessons Learned become part of the Investor Profile's accumulated investing knowledge and influence future Investment Theses, Campaign Decisions, and Campaigns.

---

# Campaign Reconstruction

### Purpose

Campaign Reconstruction analyzes brokerage transactions to infer the sequence of Campaigns pursued by the investor.

Its purpose is to organize historical trading activity into Campaigns that accurately represent the continuity of the investor's Investment Theses.

### Principles

- Trades are objective historical facts.
- Campaigns are inferred from trading behavior.
- Investment Thesis continuity determines Campaign continuity.
- Every imported Trade belongs to exactly one Campaign.
- When campaign continuity cannot be determined with sufficient confidence, CampaignIQ requests guidance from the investor.

---

# Design Principles

CampaignIQ records facts and derives state.

Permanent facts include:

- Investment Thesis
- Thesis Revisions
- Campaign Decisions
- Trades
- Investor Notes
- Reflections
- Lessons Learned

Derived state includes:

- Positions
- Cost Basis
- Cash Balance
- Campaign Performance
- Income
- Unrealized Gain/Loss
- Realized Gain/Loss

Whenever practical, CampaignIQ preserves immutable facts and derives current state from those facts.