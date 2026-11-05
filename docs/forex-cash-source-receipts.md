# Forex cash source receipts — development

Saves the exact Forex cash CSV and supplemental history as separate original document roles,
plus an owner-attributed receipt in one existing account write transaction. This checkpoint
changes no financial publication path, existing source document, credential or deployment.

The service locks the current draft, checks the displayed revision, published-month guard,
current owner and account permissions, and rebuilds the reviewed calculation from exact sources.
The receipt binds account/draft/month, both existing broker bindings, all four original source
fingerprints, policy and calculation digest, server time and owner. It expressly does not claim
independent cross-account broker verification or authorize publication.

Current saved Trade History and Forex report are retained unchanged. New source documents are
separate append-only versions. Replacements require explicit confirmation and preserve earlier
originals and receipts. An exact repeated save changes no rows or revisions. Orphan documents,
corrupt receipts and mismatching fingerprints are rejected without adoption or repair.

Reading the saved review checks permissions and current revision through the existing repeatable
read-only gateway, verifies original fingerprints, and rebuilds the complete calculation. It writes
nothing. UI review state clears when account, draft revision or candidate file bytes change;
confirmation controls are keyed to the exact reviewed digest. Server-side checks remain required.

Validation here: synthetic transaction-capable repository rollback tests and source/UI tests.
An actual restricted PostgreSQL source-save rehearsal remains required before production review.
No financial generation, reconciliation exception, publication candidate/writer or source resolution
is introduced. The pre-existing Forex publication blocker remains active.
