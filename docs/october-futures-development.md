# October futures development checkpoint

This checkpoint adds a local read-only evidence audit and upload-screen improvements.
It does not enable futures accounting, clear the current Trade History import error,
authorize publication, change broker bindings, or install production configuration.

The audit retains futures-option series separately from descriptive expiration dates,
counts every in-period leg (including partial fills), validates daily running cash
balances, and compares monthly cash and fee controls against the supplied PDF.
It makes no contract-multiplier or exercise-position assumptions. It does not validate
account ownership, full-month coverage, exchange expiry rules, or lot continuity.
The statement net figures already contain fees; subtracting total fees again would
double count them. Cash sweeps are transfers, not trading profit.

For the supplied October 2026 candidate export and statement, all five cash/fee
comparisons pass. There are 20 futures-option execution legs in four series.
The PDF statement date is October 30, 2026. This is evidence verification, not a
published performance calculation or approval to publish the month.

Remaining futures implementation must preserve separate futures and futures-option
instruments; verify contract multiplier and expiry rules; account for exercises,
resulting futures positions and settlement; reconcile independent ending positions;
and integrate provenance, monthly artifacts, account identity and publication guards.
Existing securities option semantics must not be reused silently. The live import
continues to reject the futures series code until that implementation is complete.

Crypto remains separate. The October export matches 11 execution fills to ledger
fills, but the cash control contains one-cent discrepancies and its holdings snapshot
is undated. Neither discrepancy is waived by this checkpoint. Its separate account
statement is required for independent month-end reconciliation and account association.

Upload improvements require an explicit document type, disable Save until a type is
selected, keep selections after failed saves, limit Gain/Loss confirmation to that
document type, and show Forex export dates from the selected saved month.
Account, actor, draft and revision remain part of widget keys.

Local command (requires Poppler's `pdftotext`):

```bash
PYTHONPATH="$PWD/src:$PWD" python3 -m campaigniq.futures_source_audit \
  --trade-history "/path/to/Account Trade History (3).csv" \
  --futures-statement "/path/to/Monthly Futures and Forex Report.pdf" \
  --year 2026 --month 10
```

No database connection is made by this command. Output omits account identifiers,
contains financial controls, and should be kept private. Every result explicitly
sets `diagnostic_only: true` and `publication_authorized: false`.
