# Account deployment schema foundation

Checkpoint 12219c2. This starts Stage 2 of the integration plan. It does not
activate account mode or yet supply a deployable application backend. Existing
local guards and application paths remain unchanged.

`account_schema_migrations.apply_migrations(connection)` runs inside a caller's
explicit transaction; it opens no connection and never commits. Version 1 adds
only the separate `campaigniq_accounts` schema: migration ledger, audited
directory, preferences, artifacts, drafts and original document versions.
The table name `artifact_storage_dev` retains adapter compatibility within this
separate schema. No existing user, namespace, financial record, campaign ID,
provenance, original, draft, preference or visit history is copied or rewritten.
No synthetic registry is promoted and no identity is bootstrapped.

A transaction advisory lock serializes migrations. Source checksum and catalog
fingerprint detect changed versions, columns, constraints, indexes, ownership,
privileges, policies and triggers. Unknown existing schemas cannot be adopted.
No repair, drop or downgrade is automatic. Failure must propagate and roll back.
PUBLIC receives no schema/table access. Runtime role grants are deferred to the
activation migration; the application must not run as migration owner.

## Local restore rehearsal

Run `PYTHONPATH=src python -m campaigniq.account_schema_rehearsal --development-only`.
The runner accepts only `campaigniq_restore_20261024_194114`, the current OS user
and `/var/run/postgresql:5432`. Render and routing overrides are blocked. It
requires the restored public workspace tables and refuses an existing deployment
schema. It cannot select campaigniq_dev or production.

Existing tables are SHARE locked while comparing rows. All new DDL is inside an
outer forced-rollback transaction. Savepoints test injected failure, schema drift
and a changed migration checksum. An independent read-only connection verifies
that the schema disappeared and every pre-existing table's row fingerprints
match. Reports contain no original bytes or credentials. No app restart is needed.

Unit tests validate guards and migration decisions. PostgreSQL execution is
established only by the local restore rehearsal, not container mocks. This does
not establish production readiness or backend parity. Next is the separately
configured backend and explicit trusted identity bootstrap, followed by report
and source comparison across every period. Eventual activation will require a
reviewed migration owner/runtime role policy and preserve all new user data on
code rollback. This increment has no command to drop a populated schema.
