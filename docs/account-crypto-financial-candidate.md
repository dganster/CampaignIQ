# Crypto financial candidate

The account source approval can now produce a deterministic financial candidate
through `DeploymentMonthlyApplication.crypto_generation_preview`. It uses the
current authorized read-only account snapshot and displayed document revision.
No artifact is saved and no month is published.

The candidate includes the entire statement policy calculation: exact source
fingerprints, documented opening acquisition costs, ending FIFO lots, fees
already included in cash, and the complete original discrepancy review. Owner
association is explicitly distinct from broker verification. The unmodified
pure calculation remains unapproved by itself; approval is established by the
separate, current account source receipt.

The candidate parser checks scope, fingerprints, calendar period, cash and value
roll-forwards, lot quantities and costs. Parsing alone does not establish source
authenticity. `verify_candidate` rebuilds from the current approved originals and
requires byte-identical canonical JSON. A self-consistent forged calculation or
receipt fingerprint is rejected. This API is not an authorization token.

The existing `--crypto-source-receipts` disposable database rehearsal now also
checks candidate determinism, current revision, foreign access, receipt forgery,
changed PDF rejection and actual read-only SQL enforcement. The installer runs
the complete development suite before this rehearsal.

Complete monthly artifact writing, report adapter integration and atomic
publication testing remain pending. Existing strict crypto import behavior,
financial artifacts, publication controls and production configuration are
unchanged. This checkpoint adds no UI or production activation.
