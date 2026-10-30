# Deployment account storage and bound monthly inputs

Baseline d824e4d. This integrates the deployment directory with new-account
input storage and retained existing workspace reads. It does not activate the
production application or enable publication. Existing LOCAL services and guards
are unchanged. No original document needs to be re-entered for retained access.

## Transaction and routing boundaries

`PostgresAccountDataGateway` accepts only the separate deployment directory
backend. Ordinary connections retain its explicit endpoint/TLS/role checks and
migration integrity verification. An additional grant check requires SELECT
only on retained public storage and new financial artifacts, and SELECT/INSERT/
UPDATE on new drafts and original document tables. Storage ownership, DELETE,
TRUNCATE and other extra data grants are rejected. Runtime role setup and the
recorded catalog grant transition are still a separate activation migration.
The application cannot use the migration owner connection.

An outer directory SHARE lock holds the current sign-in, permission and broker
binding stable for the entire operation. A second READ ONLY REPEATABLE READ
connection supplies report, original-document and preflight rows from one
snapshot. Read paths do not request row locks, which PostgreSQL forbids in a
read-only transaction. Uploads use a single authorized writable connection and
lock the draft before checking its displayed revision. Revocation waits until
active operations end. No mutable database handle escapes a public service call.
Internal handles expire at transaction end and caught SQL failures block commit.

New storage uses the persisted policy's account-UUID namespace in the dedicated
campaigniq_accounts schema. Legacy accounts keep their exact public workspace
namespace and are read-only. Table resolution is checked explicitly and never
falls back to public or development schemas. pg_temp is explicitly last in the
search path. Schema initialization is never a data operation.

## Reused financial behavior

`DeploymentMonthlyInputs` reuses bound upload/preflight checks, full broker
identity matching, explicit replacement confirmation and the existing published
month preservation rule. The original/source revision is checked under a lock
before saving. Every retained version of the selected draft, including superseded
originals, must match its stored SHA256 before preview or replacement. A corrupt
version is retained and use is blocked, not repaired. Broker changes are audited
with the same locked directory transition used by ordinary admin commands.

Preview and closing-inventory reconciliation reuse the existing financial
engine in read-only mode. Artifact writes are denied even inside an upload
session. There is no publish method on this new service and no alternate local
publication entry point is loosened.

`DeploymentLegacyAccountAccess` reuses the existing adapter/original inventory
comparison and canonical financial result reader. Every retained draft version
is included in inventory parity. Missing optional market coverage retains its
existing unknown meaning and is not converted into a zero subtotal. Administrator
status never confers cross-owner financial access.

## Committed local restore rehearsal

Run `PYTHONPATH=src python -m campaigniq.account_data_backend_rehearsal --development-only`.
This accepts only the previously pinned local full restore, with the unchanged
socket/current-user guard. It refuses an existing campaigniq_accounts schema.
A durable private prewrite receipt is saved before any change. All pre-existing
user tables are SHARE locked for the duration.

To exercise independent connections and actual concurrency, this rehearsal
TEMPORARILY COMMITS a new schema, a synthetic identity bootstrap and new-account
drafts/originals. The remote wrapper's connection factory is replaced only in
the test harness with the pinned local factory; no real server credentials or
identity mapping are read, and no remote endpoint is connected. The restore
owner connection supplies temporary DDL. Actual restricted runtime role grants
and remote TLS still require their separate deployment rehearsal.

Checks cover account isolation, stale and foreign drafts, replacement preservation,
110-share reconciliation, rollback after injected and caught SQL errors, SQL
read-only enforcement, expired handles, corrupt old originals, changed broker
identity, independent concurrent creates/uploads, revocation waiting, archive
and disabled access, and every retained published period against the canonical
reader. Exact pre-existing row fingerprints must remain unchanged.

After each authorized synthetic mutation, a durable checkpoint records the new
schema's row/catalog fingerprints. Cleanup locks the new tables, verifies schema
integrity and the last complete checkpoint, and removes only these exact new
tables and their now-empty schema. DROP never uses CASCADE. A changed checkpoint,
unknown object or outside dependency stops cleanup and retains evidence for
recovery. No original table or schema is selected for cleanup. An independent
read-only connection verifies the original table fingerprints and schema absence.
The restore is left unchanged on success. The running campaigniq_dev preview
uses another database and needs no restart.

The container unit tests cover routing, existing policy integration and cleanup
preconditions. The user's local PostgreSQL rehearsal establishes the actual
restore and concurrency results. This is not production publication readiness.
Next is restricted runtime role setup and publication/generation integration,
then final dashboard parity and the reviewed deployment/activation packet.

## Broker report settlement boundary

Saved result readers retain a record on the first business day after the
selected report end, using the existing attribution engine's market calendar.
Schwab's selected report period is retained; the broker close/settlement date is
not moved backward and monetary amounts are not recalculated or discarded.
Dates before the report start or after that boundary are still rejected, as
are mismatched artifact headers/markers. The rule applies to both existing and
new account result readers and uses no workspace or date-specific exceptions.
