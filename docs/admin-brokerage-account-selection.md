# Selected account month comparison (local synthetic development)

The account input preview adds Compare accounts for this month. Choose any
explicit selection of authorized accounts with saved drafts for the same month.
Each account has its own Forex/Crypto applicability choices, document binding,
draft revision, opening state, pipeline replay, reconciliation and displayed
quantities. Optional totals sum quantities only after all accounts reconcile.
Individual account results are retained even when instruments or campaign IDs
have the same names. No campaign histories, basis or P/L records are merged.
Zero net totals remain visible. A mismatch/source finding withholds totals;
missing/invalid inputs fail the whole comparison, rather than returning a
misleading partial portfolio. Empty, duplicate and unauthorized selections are
rejected; foreign IDs, wrong month and stale revisions cannot be substituted.

Two connections serve distinct purposes. The existing guarded account gateway
holds a directory share lock throughout the comparison. Before opening any
financial snapshot, it authorizes the ENTIRE selected account tuple. The second
connection uses the existing store's PostgreSQL REPEATABLE READ READ ONLY mode.
All selected draft, document and artifact reads share that single snapshot.
Administrative changes wait for the outer directory lock, while concurrent
source edits may commit but cannot mix revisions within the report. Each row
reports the exact source draft revision read at snapshot time. All short-lived
account handles expire at transaction end. The application artifact adapter also
rejects writes/deletes. These controls are development-only on campaigniq_dev.

The earlier single-account reconciler is factored into a private session-level
routine, preserving its original validation and preview_only pipeline. Both
paths return immutable summaries and remove private temporary files. There is
no publication option or production runtime switch. Quantity reconciliation is
not full financial approval, independent crypto validation, tax verification or
portfolio basis continuity. Existing market applicability and source identity
checks remain mandatory.

The SQL rehearsal seeds two brokerage accounts for one synthetic owner, bound
to different identifiers. Sources produce 110 and 220 SPY shares, total 330.
It checks distinct results, row preservation, mismatch withholding, stale/foreign
and unauthorized selections, view/revocation/archive access, and write rejection.
During one comparison it commits a source edit through a different connection:
the current report must still show its original coherent snapshot, and a later
report must see the edit. It verifies both accounts use one read-only repeatable
snapshot/backend. Test edits and original sources remain in preserved document
history. Correct versions are restored as new document versions for the ready
browser demo. Existing financial and earlier synthetic records are compared.

Run account_selection_rehearsal --development-only after the full suite. The
runner prints a private configuration for --resume. In the usual account input
preview, select Synthetic admin / Account A / January 2025. Expand Compare
accounts for this month, select Account A and Second brokerage, uncheck Forex
and Crypto for EACH account, optionally enable Show quantity totals, and click
Compare saved account months. Expected: separate 110/220 and total 330.

No real account data is read from production, migrated, deleted or published.
The existing synthetic previews, financial tables, production runtime sources
and historical recovery materials are preserved. No new schema is introduced.
