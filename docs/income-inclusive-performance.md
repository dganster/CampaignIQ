# Income-inclusive performance

This development checkpoint derives income from the exact saved brokerage statement for each published month. It does not rewrite published generations, receipts, lots, campaigns or original documents.

## Measures

- Trading realized P/L retains its existing brokerage, Forex settlement and futures definitions.
- Realized plus statement income adds posted dividends and credited interest, less identified statement interest expenses and other supported charges. Trade commissions already reflected in trading results are not subtracted twice.
- Approved Forex financing is shown separately and, when available, in an additional subtotal. Brokerage statement measures do not represent consolidated brokerage, Forex and crypto account value.
- Statement investment result is ending account value minus beginning account value minus deposits minus signed withdrawals. Income and market-value changes are already included; dividends are not added again.
- A simple period percentage is shown only for positive opening account value with no external cash flows and no noncash activity. Flowing periods have no percentage estimate. This is not a time-weighted or money-weighted return implementation or a claim of GIPS compliance.

## Evidence and coverage

Current account generations bind raw statement bytes and size to the original publication receipt. Retained historical months bind original saved documents to published provenance, including the legacy extracted-text digest. Readers recheck account access, identity, period and document checksums in the authorized read-only snapshot.

Supported statements require a complete cash summary, transaction total, exact income/expense detail reconciliation and recognized monthly dates. Verified zero income is distinct from missing or unsupported evidence. An unavailable month is unverified, never silently zero. All-period income-inclusive totals are withheld when coverage is incomplete. Unsupported distributions, return of capital, charge refunds and cross-market charges are not guessed.

Income uses statement cash-posting dates. Items remain account-level and unallocated to campaigns because a payment date does not establish ex-dividend entitlement. Campaign win rates and trading charts retain their trading-only meaning.

## Interface

Overview and Performance show income coverage and income-inclusive results. Published results and account comparison show the same source-bound income detail. Monetary values use grouping commas and two decimal places; crypto quantities retain their precision.

## Validation and release

Baseline: 8bdf71703f612321fb441b36779c94ec9f82e81f on feature/historical-backfill-2025.

Parser and integration tests cover active months, verified zero, dividends, interest, expenses, cash flows, no-trading income counted once, changed originals, identity/period mismatch, missing layouts, duplicate evidence and snapshot binding. The disposable PostgreSQL rehearsal adds actual restricted-role income reads, dashboard parity, revocation, raw SQL write rejection and receipt-protected original tampering checks. Run the full local suite and this rehearsal before committing or releasing. No production installation or publication is performed by the development installer.

Performance terminology reference: https://www.gipsstandards.org/standards/gips-standards-for-asset-owners/gips-standards-handbook-for-asset-owners/
