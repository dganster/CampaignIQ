# Read-only signed-in account closing inventory

The existing saved-input panel now offers Preview closing inventory for new
signed-in local Schwab accounts. It uses the same saved source documents,
displayed revision, broker identity checks and reconciliation engine as the
earlier synthetic preview. No input re-entry is required. Input readiness still
does not establish closing reconciliation or authorize publication.

The new entry point accepts only the exact SignedInMonthlyInputs service. The
existing synthetic reconciliation/publication constructors remain unchanged.
The new gateway reauthorizes the server-owned OIDC session, blocks legacy upload
routing and checks owner/grants/archive/disabled status. An outer transaction
holds directory and draft SHARE locks, so revoke/rebind/document replacement
must wait. The replay uses a separate guarded REPEATABLE READ READ ONLY database
connection. PostgreSQL forbids SHARE locks in a read-only transaction, which is
why the locks and read snapshot use separate connections.

The inner connection revalidates directory/audit and sign-in, uses the dedicated
new-account schema and exact namespace, and checks the revision again. Both
Python adapters and PostgreSQL deny mutations. All handles expire on success or
failure. Staging files are temporary and the engine is explicitly preview_only;
no reconciliation override, history archive or publication option is provided.

The result contains quantities, independent statement differences and source
findings. A mismatched closing quantity is displayed without repairing source
records. This does not prove complete campaign identity/basis/P&L continuity.
Neither an inventory match nor a green preview authorizes publication.

The opt-in PostgreSQL rehearsal uses NEW synthetic directory entries, accounts
and source drafts only. It verifies a 110-share complete fixture, a one-share
mismatch, wrong identities/foreign drafts/stale revisions, access revocation,
archived history, current broker binding and injected artifact/draft/raw SQL
writes. Every prior user-table row and the existing real local directory must
remain unchanged. No production connection or financial publication occurs.

Restart with the same saved sign-in configuration and existing Google client.
Select Local input test, its saved January 2025 revision, uncheck Forex/Crypto
for this stock-only fixture, and click Preview closing inventory. Expect 110
SPY shares and a match. The installer changes only development preview files,
backs up previous versions, and preserves the original production runtime.
