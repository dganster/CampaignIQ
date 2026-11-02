# Connected account futures preview

This development change connects the account's saved empty-opening confirmation,
Trade History and futures monthly PDF to the isolated futures accounting engine.
It adds no schema, grants, runtime configuration, deployment or publication action.
The ordinary securities import and published historical generations are unchanged.

## Data view

After selecting the account and month:

1. Save the full-month Trade History and the owner's empty-opening confirmation.
2. In Futures statement and accounting preview, select the monthly futures PDF.
3. Explicitly confirm that the PDF belongs to the selected account and month.
4. Save it, then choose Preview saved futures accounting.

The result separates option P/L, underlying futures P/L and fees. Transfers are
excluded from trading P/L. It labels the opening positions and statement account
association as owner-confirmed, not independent broker verification. Ending books remain calculated and may also carry a separately saved owner confirmation;
they are never relabelled as independently broker-verified. It never publishes a month.

## Identity and source boundaries

The service rechecks current server identity, owner permissions and displayed draft
revision. Only the account owner may associate a statement; view/edit grantees may
read only when their current financial access permits it. Published periods reject
statement changes. Archived authorized history stays readable; revocation and
disabled users are rechecked by the existing guarded account transaction.

The PDF must contain one consistent unmasked GMI account identifier, a matching
statement month and matching financial evidence. The GMI identifier is fingerprinted,
not exposed in the preview or confused with a securities/Forex account number.
Association to the selected account is an explicit owner assertion, not an automatic
broker-verified link. A subsequent PDF with a different GMI identifier is rejected
even when replacement is requested; it needs separate account review.

The exact PDF is saved as futures_monthly_statement_pdf_v1, with a separate server-
generated futures_statement_association_v1 document. The association contains account,
draft, month, actor, time and displayed revision, plus fingerprints of the PDF,
current Trade History, current binding and exact saved opening confirmation.
Neither role is an ordinary upload role. No existing financial original is modified.
Both new documents share one transaction. Post-write verification checks their source
bytes and association; failures roll back both. Explicit replacements retain prior
originals. Exact repeats change nothing. A corrupt prior receipt, orphan original or
missing associated PDF is blocked rather than silently adopted or repaired.

Preview uses one authorized read-only snapshot, verifies all source links, and
recomputes all cash, quantity, exercise and fee controls. A changed source, opening
confirmation, binding or PDF withholds the entire result. No stale cached partial
result or combined account total is returned. Temporary PDF/text/history files are
removed on success or failure. No raw GMI account number appears in stored metadata.

## Monthly preview and remaining publication work

The Data view now includes a read-only monthly check that combines a verified,
separate futures calculation with the ordinary securities/Forex reconciliation and
explicit crypto controls. See futures-monthly-preview.md for source lineage and the
owner's optional empty-ending confirmation. The full original history is preserved;
only a temporary labelled derivative is used for the securities check.

The deployment publisher explicitly blocks futures activity until financial generation,
report integration and database rehearsal are complete. Crypto stays separate and
cannot be omitted from the combined month's readiness check. Successful previews
retain publication_authorized=false and production_integration_enabled=false.

Unit and UI tests use synthetic sources and a transaction-capable memory fixture.
The supplied October sources were additionally evaluated with synthetic local session
metadata, without a database connection or original-file changes. This does not claim
a PostgreSQL integration rehearsal or production deployment has occurred.
