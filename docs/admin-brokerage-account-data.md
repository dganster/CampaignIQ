# Account storage and import-draft boundary: local prototype

Status: synthetic/local only. No existing dashboard, runtime, parser, publication
guard, OIDC mapping, live financial record or broker source document is changed.
The complete monthly import engine is not yet wired to this boundary.

## Transaction contract

`LocalAccountDataGateway` accepts only the guarded local PostgreSQL directory
service. A transaction receives a server-authenticated identity and account ID,
never an account selector's supplied workspace, namespace or cached permission.
It locks the current directory row in SHARE mode, verifies the directory/audit
ledger and synthetic identity binding, and authorizes the selected account.
Administrative commands require an incompatible row lock. They therefore wait
for an already-authorized transaction to finish, and later transactions observe
the committed revoke, disabled user or archived account.

Artifacts and existing `ImportDraftRepository` methods are bound to that same
connection and account-specific physical scope. Their inner context managers
do not commit. An outer exception rolls back artifacts, drafts and uploaded
document versions together. A caught PostgreSQL error poisons the session so a
caller cannot mistake an aborted transaction for a successful partial commit.
Handles are invalidated before leaving the outer transaction and cannot be
reused as stale authorization. Namespace/workspace properties cannot be reassigned.

View sessions block artifact writes/deletes and draft create/upload/abandon
operations. Expected missing-file lookups remain normal read behavior. Read
handles use a regular transaction to acquire the directory lock; the trusted
server adapter enforces read-only operations. This is not PostgreSQL RLS or a
sandbox against malicious Python code with raw database access.

## Isolation and existing behavior

The separate `campaigniq_accounts_dev` schema contains only test artifacts and
drafts. A restricted transaction search path and relation-schema checks prevent
fallback to public tables. Physical test scopes combine registry UUID and
account UUID so even two demo registries with identical logical legacy scopes
cannot share rows. Logical legacy account bindings remain unchanged in the
directory; no production data is relabeled or migrated into this test schema.

The adapter reuses current artifact-key validation and draft upload controls:
same-month draft uniqueness is scoped per account; replacing a different file
requires confirmation; original document bytes and superseded versions remain.
Foreign draft UUIDs cannot retrieve or replace another account's documents.

The adapter is trusted infrastructure, NOT a raw browser upload/publication
endpoint. Real imports must still satisfy file/account evidence, reconciliation,
continuity, publication and rollback requirements. In particular, filesystem
side effects cannot be rolled back by PostgreSQL; the full engine must stage
files before entering an eventual database commit transaction.

## Verification

25 offline boundary tests cover read-only operations, stale/closed handles,
cross-account authorization, schema fallback refusal and transaction ownership.
An opt-in PostgreSQL test and the explicit runner exercise 12 actual controls:

```
PYTHONPATH=src python -m campaigniq.account_data_rehearsal --development-only
```

The runner writes only newly identified synthetic partitions. It hashes existing
financial tables and earlier synthetic account partitions before/after. It
observes an actual PostgreSQL blocking relationship during concurrent revocation,
and tests atomic rollback of artifact/draft/document writes and caught SQL errors.
Reports and registry IDs are retained under CampaignIQ-Records/Development-Rehearsals.
No schema/table drop, data reset, production connection or publication is performed.

Next integration: route the existing monthly import preview and source evidence
through authorized account contexts, then rehearse an explicit legacy-data plan
before considering a live runtime/account-selector switch.
