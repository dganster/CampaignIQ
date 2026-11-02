# User-confirmed empty futures opening book

The deployment account input service can record the owner's explicit assertion
that neither outright futures nor options on futures were open at the preceding
month end. This is user-supplied evidence, never independent broker verification.
It is not inferred from the absence of trades, a zero cash balance, or a chat.

The source-only development change adds an optional Futures opening positions
panel in the account Data view. No live configuration or deployment is included.
The panel shows the preceding calendar date, requires an explicit checkbox, and
offers a read-only status check. Published and view-only accounts cannot save it.
The server also rejects edit grantees: this particular assertion is owner-only.

The server checks the current sign-in and owner permissions inside the existing
account transaction, locks the draft, checks the displayed revision, and rejects
published months. The saved Trade History must identify the currently bound
securities account, declare coverage of the complete selected month, and contain
supported in-period futures-option executions. This does not independently identify
the separate futures account or validate a futures PDF.

The generated confirmation contains account and draft IDs, year/month, prior
month-end date, server actor and time, the displayed source revision, the exact
Trade History SHA256 and current binding digest. It contains no full broker number.
Its evidence type is always user_confirmation and broker_verified is always false.
Callers cannot provide their own actor, timestamp or receipt body through this API.

One metadata document role, futures_empty_opening_confirmation_v1, is saved in
the existing account draft-document table. It is outside the ordinary upload-role
allowlist. No schema, database grant, financial artifact or source original changes
are required. Post-write validation occurs inside the same transaction. Failure
rolls back; exact repeats do not create revisions. Explicit reconfirmation after
a source change preserves the previous confirmation as a superseded original.
Malformed, ambiguous, wrong-scope or corrupt prior confirmations block reads and
writes rather than being repaired or overwritten automatically.

Read-only status uses the existing authorized account snapshot. Changing the
Trade History or account binding invalidates the old assertion. Revocation and
disabled users are rechecked; archived authorized history remains readable.

## Remaining integration

This change does not make the securities parser accept futures series codes,
establish independently verified ending positions, or integrate the futures PDF,
futures identity and financial generation into publication. The isolated futures
accounting preview still reports its opening-state assumption. The validated
confirmation is now available through the account input service for that next
integration. Neither the confirmation nor status response authorizes publication.
No October month is published by installing, saving or checking this confirmation.

## Verification

Regression tests cover owner-only writes, exact repeat behavior, stale revisions,
cross-account/draft access, foreign/missing/incomplete sources, changed bindings,
corrupt or duplicate JSON fields, post-write rollback, view grants and revocation,
archived history, disabled users, and explicit UI confirmation. They use synthetic
documents and the existing transaction-capable test fixture. A production database
rehearsal and release remain separate; this package claims neither was performed.
