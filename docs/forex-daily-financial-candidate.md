# October daily-session Forex financial candidate

The account Data workflow can now calculate a monetary candidate directly from
the saved daily PDFs and cash originals, verified against their existing owner
receipts. This is a financial calculation, not an importer repair or a new
publication policy. The source/coverage confirmations do not approve this new
accounting proposal. No October Forex monthly statement is required.

The candidate rechecks the account owner, current authorized account/draft
revision, source hashes, separate broker bindings, exact published predecessor
and every daily inventory control. The close of the carried USD/JPY lot retains
its published lot/campaign identity. In-month opening/closing quantities are
replayed provisionally; the monthly writer has not assigned or saved their
campaigns. No lots are seeded when the published predecessor is absent.

For each pair and daily reporting boundary, new close P/L equals the day's
settled P/L minus the previous pending-close amount plus the new pending-close
amount. Pair tables must match the daily summary and Closed Positions dollars.
Every close must have exactly one matching execution, cash reference, quantity,
rate and position effect. Multiple closes for one pair in a single daily report
are withheld until their dollar allocation is independently supported. A zero
P/L close remains an explicit execution. Starting/final pending settlements,
non-flat final positions, external daily cash movements and separate nonzero
fees are outside this October candidate and are rejected.

Financing is matched by exact reference and amount to saved Forex cash rows;
an explicitly named cash pair must also agree. Both source timestamps are
retained without asserting a timezone conversion. Every calendar financing
reference must occur in the daily-session detail. Financing from September 30
is separated from October calendar financing. The original CSV's financing
amount and delivery amount must agree. Additional US$ rows are labelled as
securities cash only when reference and amount match exactly; unmatched rows
remain explicit and unclassified. No stored CSV row is excluded or relabelled.

Both the reporting-session and original calendar cash equations must reconcile
exactly, including the equal-and-opposite opening-cash and trade-dollar source
differences. No tolerance absorbs pennies. The original MTD/YTD/row totals,
reported settlement dates, omitted close references and all saved-history cash
differences remain in the account-scoped candidate. An absent original
settlement date remains null. It is never inferred from a later cash report.

The final daily position must be flat and later export balances/activity must
remain unchanged. That later cash evidence is labelled separately; it does not
extend independent daily coverage or imply that missing reports were issued.

`forex_financial_candidate_preview` runs in the existing read-only account
transaction. The UI action is **Calculate saved Forex financial candidate** under
the daily Forex source review. It needs saved originals, not a new upload. The
record binds both source receipts, all original hashes, broker-binding hashes,
predecessor fingerprints, the current revision and a deterministic calculation
hash. Verification rebuilds the entire record in the current authorized snapshot.
Self-consistent or edited candidate JSON never grants authority.

The installer runs the full suite and extends the existing disposable LOCAL
PostgreSQL clone rehearsal to verify exact monetary reconstruction, foreign and
revoked access, stale revisions, and database-enforced read-only transactions.
Source corruption withholds both saved review and financial candidate without
adoption or repair. Existing copied financial rows and the retained restore must
remain unchanged. Production is not contacted by this installer.

## Remaining publication work

The original strict Forex importer and combined monthly publication checks are
unchanged. A durable owner approval of the proposed monetary basis, writer/report
integration and a complete generation rehearsal are still required before this
candidate can authorize publication. All returned financial-policy approval,
financial-state-written and publication-authorized flags remain false. This
checkpoint does not claim that October is publishable.
