# Owner approval of the October daily-session monetary basis

This checkpoint adds a durable owner approval of the exact saved-source Forex
financial candidate. Source association and daily coverage receipts remain
separate evidence. They do not become monetary approvals automatically.

The owner first calculates the saved financial candidate. The review displays
the proposed close amounts, financing and cash totals, original monetary
differences and the last independent daily boundary. The owner must explicitly
accept the daily reporting-session basis, the displayed amounts with original
CSV differences retained, and the daily/export coverage limits. Unclassified
original financing rows prevent approval. No October monthly Forex statement is
required and no missing report is presumed to have been issued.

`save_forex_financial_policy` rechecks the displayed revision and full candidate
hash in a serialized account write transaction. It requires the current account
owner; an edit grant is insufficient. Published months cannot receive an approval.
The exact current financial calculation is rebuilt from original daily PDFs,
cash/report sources, both owner source receipts, broker bindings and canonical
published opening lots before any approval row is written.

The new `forex_financial_policy_approval_v1` document stores owner attribution,
a timezone-aware timestamp, explicit choices, the reviewed candidate/revision,
and a stable calculation/source fingerprint. It occupies one append-only draft
document role and increments the source revision once. Its original candidate
hash is reverified by reconstructing the reviewed revision after the approval
upload increments the draft revision. Only the top-level revision is omitted
from the stable hash. Every original source, receipt, binding, predecessor,
monetary amount and coverage limit remains bound.

A repeat with a newly reviewed current candidate is a no-op. A stale displayed
hash is rejected. Unrelated draft revisions can preserve a valid approval when
the entire bound candidate is unchanged. Changed or corrupt sources/approval
records withhold the saved approval; this checkpoint offers no overwrite,
adoption or repair path. A legitimately changed approved source set requires a
separate explicit supersession workflow before it can be approved again.

`forex_financial_policy_preview` rebuilds and verifies the receipt in an authorized
read-only account transaction. View grants can read it, edit grantees cannot
approve it, and revoked access is rechecked. Existing source/candidate records
retain their original unapproved/provisional flags; the separate saved policy
review identifies the owner approval. Original CSV differences are not marked
resolved and cross-account broker association is not claimed to be independently
verified.

The local disposable PostgreSQL rehearsal now checks failed writes and forbidden
SQL rollback, concurrent duplicate approvals with exactly one winner, repeatable
readers seeing no partial approval, independent approved-source reconstruction,
current permissions, and read-only SQL enforcement. A corrupt approval is withheld
and cannot be overwritten as repair. Existing financial artifacts and the
retained restore must remain unchanged. The installer runs the complete suite
before this rehearsal and makes no production connection or deployment.

## Remaining integration

This approval writes source-policy evidence only. It neither creates financial
artifacts nor authorizes monthly publication. The strict Forex importer and
combined monthly publication gate remain unchanged. The final monthly writer
must consume the verified approval, preserve original source differences and
exact lot/campaign identity, and pass the complete generation rehearsal before
October can be published. Publication remains a separate explicit account/month
action even after that integration.
