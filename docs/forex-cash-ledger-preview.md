# Forex cash ledger development preview

October 2026 only. Read-only source review; not an accounting approval or publication path.

Requires full month cash boundaries, exact row balance arithmetic, supported entry types,
unique references and exact Forex execution timestamp/pair/quantity/price parity. A separate
pair-filtered execution source can supply pairs absent from the base execution table; overlapping
pairs are rejected. All source SHA256 values and original monetary discrepancies are retained.
No credentials, identity bindings, authoritative opening inventory or production connection are used.
Account identity and closing inventory are explicitly unverified. Cash and execution sections from
one export are corroborating sections, not independent broker verification. No existing publication
or reconciliation guard is changed. These candidate dollar results are not tax calculations.

Real source review: full-year USDJPY.csv cash ledger balances; saved October history omits USD/JPY
from its execution table but includes its cash close. Supplemental filtered execution supplies that
close. Original USD/CAD cash amount differs from its running balance by 2.97. Transaction report
omits the USD/JPY reference and has different settlement/header amounts. Preserve all sources.
