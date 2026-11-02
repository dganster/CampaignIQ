# Futures financial record and report adapters

This development checkpoint builds a canonical financial candidate from the current
account's verified original Trade History, exact futures PDF, statement association,
and explicit empty opening and ending confirmations. It performs no publication.

The USD profile supports the previously verified flat-book ES options calculation
with a $50 multiplier. The record retains every execution and delivery, exact decimal
amounts, source fingerprints and derivative lineage. Loading replays the journal and
checks its result against the stored financial components. Funding sweeps are
transfers, excluded from net realized P&L. Fees are included exactly once.

Opening and ending positions and statement account association remain owner
confirmations. They are never labeled independent broker position verification.
The read-only account endpoint requires a current authorized snapshot and revision.
It cannot save the candidate or authorize publication.

Report adapters accept a futures component only with an explicit finalized-month
marker and matching account publication receipt, exact record checksum, account,
draft, revision and source references. A conflict withholds the result. Account
result loading additionally rebuilds the candidate against current saved originals
and evidence. Existing months without a futures component retain their prior totals.

Monthly and multi-month realized totals add the validated futures net once. Futures
remain separate from campaign counts, campaign win rates and campaign drawdown;
those measures are not represented as covering futures. Crypto stays separate.
No artificial campaign identities are created. Conditional report UI labels explain
this scope, without adding another upload or technical generation panel.

Tests use synthetic finalized storage to verify report integration, receipt conflicts,
tampered executions, source lineage, evidence labels, precise totals and read-only
behavior. An isolated check of the supplied October sources reproduced 20 option
execution legs and net futures/options result of -3793.60 with source bytes intact.
That source check does not establish production account authorization or whole-month
securities/crypto reconciliation.

The deployment publisher's futures guard remains unchanged and closed. Complete
generation writing, account report integration under actual restricted PostgreSQL
roles, and crypto evidence/reconciliation are still required before October can be
published. No schema, role, runtime configuration or production deployment changes
are part of this checkpoint.
