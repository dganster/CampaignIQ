# ES futures-option accounting preview

This local development engine is separate from the existing securities option,
Forex, and crypto pipelines. It cannot save documents, alter bindings or financial
records, deploy code, or publish a month. The existing live import remains unchanged.

## Supported evidence

The engine narrowly supports quarterly ES futures underliers and European ES
weekly/end-of-month option series. The multiplier is explicitly $50 per index
point, rather than inherited from stock options or inferred from a string suffix.
Other products, including Micro E-mini and SME month-end contracts, are rejected.
Broker series codes and descriptive dates are distinct fields; date parsing never
attempts to interpret a series code as an ordinary calendar date. Expiration-month
consistency is checked, but exchange holiday/expiry-calendar verification is pending.

Source execution rows preserve order boundaries, row identities and partial fills.
Every source option order must independently match ledger premium cash by timestamp,
series and calculated cash, with multiset matching rather than deduplication.
The preview also matches PDF transaction quantities by date, series, strike and side.
Consolidated PDF rows may match multiple source fills without dropping those fills.

## Exercise and cash accounting

FIFO option lots prohibit invented opening basis, over-closes and silent position
flips. Exercised calls deliver futures in the option position's direction; exercised
puts deliver in the opposite direction. Each source exercise must uniquely match an
outstanding option and its adjacent broker delivery cash row. Delivered futures
offset FIFO at their strike entry prices; calculated cash P/L must equal the broker
cash entry. Nonzero unexplained delivery cash is rejected, not assigned to premiums.

The PDF reader checks exercise series, strike and quantity, futures contract month,
offset quantity, P/L, fees, sweeps and individual cash activity. Memo P/L rows do not
create duplicate trades or cash. Unknown dated activity and unsupported layouts fail.
Cash sweeps remain transfers. Fees are counted once; net statement subtotals already
include fees. Fee allocation between option premiums and futures exercise activity
is checked against the individual PDF rows.

Synthetic example: $200 gross option premium, -$1,000 realized futures result and
-$16 fees produce -$816 net trading result. A $1,000 funding sweep changes cash,
but does not turn that trading loss into a profit.

## Explicit remaining boundaries

The preview assumes an empty opening futures book and labels that assumption in
every successful result. A zero opening cash balance does not prove zero positions.
Calculated empty ending books do not independently prove a month-end position
statement. Account identity, verified opening/ending state, trading-calendar rules,
ordinary outright-futures execution and daily variation-margin support, account
storage, campaign reporting and guarded publication integration remain pending.
The result always sets `publication_authorized` and `production_integration_enabled`
to false, including source-failure results. Crypto stays separate.

## Local use

Requires the existing development environment and Poppler `pdftotext` for PDF reading:

```bash
PYTHONPATH="$PWD/src:$PWD" python3 -m campaigniq.futures_accounting \
  --trade-history "/path/to/Account Trade History (3).csv" \
  --futures-statement "/path/to/Monthly Futures and Forex Report.pdf" \
  --year 2026 --month 10 --development-only
```

Output contains private financial amounts and source fingerprints, but no account
numbers. Missing or invalid sources return a JSON failure and a nonzero exit code.
Successful exit means a development preview was computed, never approval to publish.

## Contract-rule references

Reviewed November 2, 2026; retained here as explicit implementation references:

- [CME weekly and EOM ES options FAQ](https://www.cmegroup.com/trading/equity-index/weekly-eom-options-faq.html): exercise into quarterly futures, call/put exercise treatment and settlement distinction.
- [CME ES futures overview](https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.performance.html): $50 contract unit.
- [CME SME versus ES month-end comparison](https://www.cmegroup.com/articles/faqs/sanp-500-month-end-futures-and-options.html): distinct $100 SME and $50 EW products and underliers.
- [CME European series codes](https://www.cmegroup.com/articles/faqs/e-mini-s-p-500-tuesday-and-thursday-options-frequently-asked-questions.html): weekly series families and EW end-of-month codes.
