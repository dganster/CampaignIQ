# CampaignIQ Domain Model

## Purpose

CampaignIQ models investing as investors think about it rather than as brokerage firms record it.

Brokerage firms organize investing into accounts, positions, and transactions.

CampaignIQ organizes investing into campaigns.

The purpose of the CampaignIQ domain model is to define the language of investing as understood by CampaignIQ. It establishes the concepts, relationships, and rules that govern the system and serves as the foundation for every software design decision.

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

CampaignIQ uses the Investor Profile to understand how an investor's philosophy and behavior evolve over time.

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

---

## Campaign

### Definition

A Campaign is the complete lifecycle of implementing an Investment Thesis.

A Campaign begins when an investor commits capital in support of an Investment Thesis.

A Campaign may include any number of Campaign Decisions, Trades, Positions, Cash Transactions, Notes, and Thesis Revisions.

A Campaign concludes when the investor determines that the Investment Thesis has been completed or abandoned.

A Campaign is the primary organizational object within CampaignIQ.

---

## Campaign Strategy

### Definition

A Campaign Strategy describes the method by which an Investment Thesis is implemented.

A strategy defines how an investor chooses to express an Investment Thesis in the marketplace.

Multiple Campaign Strategies may be appropriate for the same Investment Thesis.

### Examples

Examples include:

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

CampaignIQ uses Campaign Strategies to classify Campaigns, compare similar Campaigns, and help investors understand which strategies have historically been most effective for particular types of Investment Theses.

---

## Campaign Decision

### Definition

A Campaign Decision is an intentional action taken by the investor to advance, modify, or conclude a Campaign.

Campaign Decisions capture the investor's reasoning and intent.

One Campaign Decision may result in one or more Trades.

### Purpose

CampaignIQ models investor decisions rather than merely recording brokerage transactions.

Campaign Decisions provide the context necessary to understand why Trades occurred.

### Examples

Examples include:

- Initiate Campaign
- Increase Exposure
- Reduce Exposure
- Roll Position
- Generate Income
- Hedge Risk
- Take Profits
- Exit Campaign
- Abandon Thesis

---

## Trade

### Definition

A Trade is an executed brokerage transaction that implements a Campaign Decision.

Trades are objective historical facts recorded exactly as executed by the brokerage.

Trades open, modify, or close Positions.

Every Trade belongs to exactly one Campaign.

---

## Position

### Definition

A Position represents an ownership or contractual interest in a financial instrument resulting from one or more Trades.

A Position may be opened, increased, reduced, closed, assigned, exercised, or expire.

Positions exist only within the context of a Campaign.

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

A Campaign normally focuses on a single underlying Security.

---

## Investor Note

### Definition

An Investor Note is a dated observation recorded during the life of a Campaign.

Notes capture information that cannot be inferred from brokerage transactions.

Examples include:

- Changes in market outlook
- Research findings
- Emotional reactions
- Strategy adjustments
- Earnings observations

Investor Notes become part of the permanent history of a Campaign.

---

## Campaign Outcome

### Definition

A Campaign Outcome records the objective results of a completed Campaign.

Typical measures include:

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

A Reflection compares the original Investment Thesis with the Campaign Outcome and answers questions such as:

- Was the thesis correct?
- Was execution disciplined?
- What surprised me?
- What would I do differently next time?

Reflections explain why the Campaign produced its outcome.

---

## Lesson Learned

### Definition

A Lesson Learned is durable investing knowledge derived from one or more completed Campaigns.

Lessons Learned become part of the Investor Profile's accumulated investing knowledge and influence future Campaign Decisions.

Campaigns generate Lessons Learned.

Investors own them.

