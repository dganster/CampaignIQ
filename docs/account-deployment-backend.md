# Deployment account directory and explicit bootstrap

Baseline 0cad0cf. This is the separate directory component of Stage 2. Existing
LOCAL services, their guards and UI remain unchanged. No production launcher,
activation command, role-grant migration or deployable financial gateway is
introduced by this increment.

## Trusted configuration and connections

`AccountDirectorySettings` requires one explicit stable registry UUID, an exact
HTTPS issuer and the complete canonical subject-to-legacy-workspace mapping.
The mapping is immutable and ambiguous ownership is rejected. No registry is
regenerated on startup and no account ownership is inferred from a name/email.

`AccountDatabaseTarget` pins the remote hostname, database, port and runtime
role. The supplied server DSN must match and use verify-full TLS; service,
hostaddr, options, multi-host and routing overrides are rejected. The constructor
opens no connection. Credentials are not included in object representations or
receipts. This target is independent of the strict existing local guard, which
still accepts only campaigniq_dev on the local socket.

`PostgresAccountDirectory` opens operation-scoped connections. Reads use a
read-only repeatable snapshot. Every connection pins search_path to pg_catalog,
checks the actual database/session role, rejects elevated/schema-owner roles,
checks metadata privileges and verifies the migration checksum/catalog signature.
The expected metadata grants are SELECT/UPDATE on directories, SELECT/INSERT on
audit_events, and SELECT on schema_migrations; INSERT on directories, audit
rewrites/deletes, ledger writes and metadata TRUNCATE are prohibited. Runtime
role creation/grants and their recorded catalog transition belong to the later
activation migration. The app cannot run as the migration owner. No schema
initialization or bootstrap method exists on this runtime service.

The caller passes only Streamlit's server-owned user object. Session objects in
the SQL rehearsal are synthetic test substitutes, never a browser actor selector.
Each operation reloads the directory, checks its checksum and independent audit
ledger, and verifies the entire trusted legacy mapping. Administrator status
confers no financial access. The same existing immutable policy implements stale
revision, permission, disable, archive, grant, last-admin and ownership rules.
Commands lock the current directory, authorize that server session under the
lock, compare the displayed revision, update state and append the audit event in
one transaction. A caught SQL error prevents commit. No live handle escapes a
public operation; internal DirectoryTransaction handles reject expired or failed
transactions and are trusted service internals, not browser capabilities.

## Explicit bootstrap

`review_bootstrap` builds a deterministic pristine legacy-owner proposal from
explicit trusted owner records and the designated administrator identity. It
checks the complete set of restored workspace namespaces and fingerprints all
three public workspace tables, including shared records, abandoned drafts and
superseded document versions. This does not write or infer real identities.

`bootstrap_directory` requires the exact reviewed plan checksum and matching
server settings. It revalidates the pristine proposal, locks the source tables,
rechecks their fingerprints, and inserts only the new directory metadata. A
second registry or differing existing directory is rejected, never overwritten.
An identical initial bootstrap is idempotent. An active caller-owned transaction
is mandatory; the function never commits, creates schemas, changes financial
records or runs from login. Bootstrap against production is not wired or
approved by this package.

## Local full-restore rehearsal

Run `PYTHONPATH=src python -m campaigniq.account_backend_rehearsal --development-only`.
The existing fixed local-restore connection guard is reused unchanged. All DDL,
synthetic directory metadata and audited commands run inside a forced-rollback
transaction. Synthetic issuer/subjects are assigned explicitly to retained
workspace namespaces solely to exercise the adapter; no actual identity mapping
is inferred or deployed. A nested bootstrap failure and audit append failure
must roll back. Tests cover mapping stability, current server identity, owner
isolation, stale/unauthorized commands, grants/revocation, archive/disable,
last-admin protection, and corrupt directory/audit/configuration rejection.
Existing rows remain unchanged. An independent read-only connection after the
outer rollback confirms that all original table fingerprints match and the new
schema is absent.

The rehearsal exercises the same DirectoryTransaction code used by the remote
runtime wrapper, with the restore owner's connection only for temporary DDL.
It does not claim to test remote TLS, a deployed runtime role or committed
multi-connection behavior. Those remain part of the restricted role migration,
backend gateway integration and activation rehearsal. All-period report/source
parity and the production release packet are still required before activation.
No code rollback should delete new tables or post-activation user records.
