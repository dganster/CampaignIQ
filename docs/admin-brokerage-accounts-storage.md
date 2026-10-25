# Development directory persistence

This increment is not activated in the application. Importing modules or
installing source does not connect to a database. An explicit rehearsal uses
only `campaigniq_dev`, the current OS user, port 5432, and the standard local
Unix socket. Remote hosts, Render, service/host-address overrides, non-development
databases and alternate users are blocked before connecting and rechecked on
every connection. No production environment settings are changed.

## Representation and transactions

The dedicated `campaigniq_admin_dev` schema contains directory snapshots and a
separate per-revision activity ledger. Each snapshot stores validated users,
accounts, grants and immutable policy events in versioned JSONB. This is an
initial document representation, not yet a normalized users/accounts schema.
Strict decoding rejects missing/unknown fields, malformed flags, invalid
identifiers, duplicate identities/scopes, dangling grants and missing admins.

An administration command locks the current directory row, checks the actor's
current enabled/admin status and expected revision, computes the policy change,
replaces the snapshot with compare-and-swap, appends one audit event, verifies
the resulting snapshot/ledger, and commits together. Exceptions roll back both.
No caller-supplied replacement snapshot is accepted by the command interface.
Bootstrap is explicit and cannot overwrite an evolved or different directory.

Reads use a consistent read-only database snapshot, validating both directory
checksum and activity history. Disabling/revoking a user affects the next
service call rather than relying on browser session flags. Checksum validation
detects accidental corruption; it is not tamper-proof against a database
administrator who can rewrite both content and checksums.

This service does not yet protect financial reads/writes: account storage is
not wired to it. A future financial transaction must reauthorize within its
own transaction, not trust a previously returned account context indefinitely.
The internal snapshot API must not become a browser-facing user directory.

## Actual local rehearsal

Run `PYTHONPATH=src python -m campaigniq.account_directory_rehearsal --development-only`.
The default connection is `dbname=campaigniq_dev` with explicit socket/user
normalization. Optional `CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL` must pass the same
local restrictions. Never set it to a Render URL.

The runner creates synthetic users and test directory partitions, and retains
them locally for inspection. It never migrates existing financial data, deletes
records, or drops tables. Fourteen controls verify independent persistence,
account isolation, view/edit/revocation, rejected commands, injected transaction
rollback, concurrent commands, stale revisions, disabled users, archive behavior,
admin handover, corrupted ledger/snapshot rejection, registry isolation, and
unchanged hashes/counts for all existing non-system tables outside the test
schema. Intentional corruption tests restore only their own synthetic rows.

Reports go under `CampaignIQ-Records/Development-Rehearsals`. A failure report
retains the registry ID and successful checks. No automatic reset is performed.
The PostgreSQL pytest test is opt-in using the separate admin test variable;
the explicit runner provides the database rehearsal without changing shell
configuration or enabling unrelated tests.

Next integration: persistent admin/account UI and identity bootstrap, per-account
financial storage/drafts/publication/cache routing, and verified legacy-data
compatibility. Production use still requires a reviewed activation plan.
