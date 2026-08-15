# Historical Completeness and Boundary Resolution

## Purpose

CampaignIQ must never silently treat incomplete historical data as complete. When a user begins using CampaignIQ in the middle of an existing trading history, the system must determine whether the supplied documents are sufficient to establish the beginning state and the ancestry of campaigns that continue into the selected month.

If information is missing, CampaignIQ should identify exactly what is missing, explain why it matters, and give the user a choice: provide the missing history or deliberately exclude the affected cases and proceed.

## Standard monthly input

For a month being reconstructed, the normal broker inputs are:

1. **Account Trade History** — executed trading activity.
2. **Brokerage Statement** — authoritative account/position state, including beginning/ending positions, assignments/exercises and pending/open activity where reported.
3. **Realized Gain/Loss report** — broker-reported cost basis and realized P&L for closed transactions; this is a validation/cost-basis source, not the primary transaction source.

The three sources have different roles:

- Account Trade History → **what trades occurred**
- Brokerage Statement → **what the account actually contained**
- Realized Gain/Loss → **how the broker accounted for closed positions**

The Realized Gain/Loss report is not assumed to contain every closed transaction; broker documentation explicitly describes it as summary information.

## The first month is special

A new user may begin CampaignIQ after campaigns have already been running for months. The immediately preceding month's statement is the preferred source for opening inventory, but it is not always sufficient.

CampaignIQ must trace unresolved positions and campaigns backward until their ancestry can be established or until the available historical data ends.

Therefore the historical requirement is **evidence-driven**, not a fixed number of months.

Do not tell a user to supply twelve months of statements merely because January is the first month being imported. Ask only for the months and document types needed to resolve the actual gaps.

## Boundary validation

Before treating a monthly reconstruction as complete, CampaignIQ should run a `BoundaryValidator` (name provisional) that evaluates at least:

- opening inventory completeness
- trading-activity completeness
- ending-inventory completeness
- realized-P&L completeness
- pending/unsettled activity
- non-trade economic events such as assignments/exercises
- unresolved position ancestry
- unresolved campaign ancestry

A conceptual result is:

```text
BoundaryValidation
    period
    opening_inventory
        status: COMPLETE | MISSING | PARTIAL
        source
    trading_activity
        status: COMPLETE | MISSING | PARTIAL
    ending_inventory
        status: COMPLETE | MISSING | PARTIAL
        source
    realized_pnl
        status: COMPLETE | MISSING | PARTIAL
    pending_activity
        status: NONE | PRESENT
    unresolved_positions[]
    unresolved_campaigns[]
    required_user_action[]
    warnings[]
```

## Campaign ancestry resolution

A position that exists at the beginning of the selected month may belong to a campaign that started much earlier. CampaignIQ should not silently truncate that campaign at the start of the user's data window.

When an opening position cannot be explained from the supplied history, CampaignIQ should walk backward:

```text
Selected month
    ↓
Unresolved opening position/campaign
    ↓
Previous month
    ↓
Still unresolved?
    ↓
Earlier month
    ↓
Continue until origin is established or available history is exhausted
```

There are two related but distinct questions:

### Position ancestry

When did the position itself enter the account, and what quantity/lots were carried into the selected month?

### Campaign ancestry

Is the position part of an earlier campaign involving opens, closes, rolls, assignments, or other linked activity?

The system may need different amounts of historical data to resolve these two questions.

## User-facing missing-data workflow

Missing historical information must produce an actionable user alert, not an internal diagnostic such as `started_before_data`.

Example:

> ## Additional historical data required
>
> CampaignIQ can import the January 2026 activity, but it cannot establish the complete history of 3 campaigns.
>
> - COIN — earliest unresolved activity: October 2025
> - LIN — earliest unresolved activity: November 2025
> - NFLX — earliest unresolved activity: December 2025
>
> Please provide the relevant Account Trade History and Brokerage Statement files for October–December 2025. CampaignIQ will only request additional months if the supplied history shows that they are necessary.

The message should identify:

- affected position/campaign
- earliest unresolved date
- required month(s)
- required document type(s)
- why the information matters
- what happens if the user cannot provide it

## User choice when data is unavailable

A user may be unable to retrieve older brokerage data. CampaignIQ must allow them to proceed without forcing a false reconstruction.

The user should receive two explicit choices:

### Provide historical data

CampaignIQ specifies the months/documents needed. After import, it reruns historical completeness and reconstruction.

### Exclude unresolved cases and proceed

CampaignIQ excludes only the affected positions/campaigns and continues with everything that can be established reliably.

The exclusion must be explicit and visible. CampaignIQ must not silently discard the cases.

Example:

> **3 campaigns will be excluded.**
>
> They will not be included in campaign statistics, realized-P&L attribution, performance metrics, or other calculations that depend on their missing history.

## Excluded is not the same as resolved

Maintain explicit states such as:

- **Resolved** — sufficient evidence; included normally.
- **Unresolved / awaiting data** — additional history is required and the user has not yet chosen to exclude it.
- **Excluded by user** — deliberately omitted because the required history is unavailable.

Excluded cases should remain discoverable and recoverable. If the user later supplies the missing history, CampaignIQ should be able to re-run the resolution and offer to restore the case.

## Completeness reporting

Every reconstruction should report its historical completeness. For example:

> **Reconstruction completeness: 94%**
>
> 47 campaigns reconstructed
> 3 campaigns excluded because historical data was unavailable
> 0 unresolved campaigns included in results

The exact metric and denominator remain to be designed; the principle is that users must be able to see whether the results are complete or intentionally partial.

## Boundary conditions are not necessarily errors

CampaignIQ must distinguish missing data from legitimate cross-month boundary behavior.

For example, a trade may execute on January 30 but settle February 2. Schwab can show the transaction in pending/open activity while it remains in the January 31 inventory. In that case:

- the trade belongs to January execution/campaign chronology;
- its position effect occurs after the January 31 inventory cutoff;
- it should not be reported as missing data.

The user-facing report should explain such cases as boundary notices rather than errors.

## Design principle

A campaign should never be silently truncated merely because the user began using CampaignIQ after the campaign began.

For every affected case, CampaignIQ must do one of three things:

1. **Resolve the ancestry** using supplied historical evidence.
2. **Explicitly classify the campaign as externally inherited/unresolved** if the product later supports that mode.
3. **Tell the user exactly what historical evidence is needed, and allow the user to exclude the case if that evidence cannot be obtained.**

The product must preserve the distinction between what is known, what is inferred, what is missing, and what the user deliberately chose to omit.

## Next implementation checkpoint

Before lot/basis attribution proceeds, implement the boundary/historical-completeness workflow and tests for:

- complete first-month onboarding
- missing immediate prior-month data
- campaigns requiring multiple months of backward tracing
- exact document/month requests
- user exclusion of unresolved cases
- later restoration after historical data is supplied
- pending settlement as a legitimate boundary condition rather than missing data
- completeness reporting
