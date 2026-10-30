# Deployment publication integration — development verification only

Baseline 5861577. No UI switch, production connection, activation, role/password
provisioning or deployment command is introduced. Existing LOCAL publishers and
their local-target guards are unchanged.

`DeploymentAccountPublisher` requires the exact deployment monthly-input service
and a separate server-owned publication connection. Its trusted issuer/registry
configuration and database host/name/port must match the ordinary input backend;
the role must differ. A browser cannot select a connection or supply identity
claims. The public method requires literal confirmation, displayed source revision
and boolean market applicability. It reloads current server sign-in and the audited
directory under a SHARE lock, checks write access and a new Schwab account, fixes
the exact dedicated schema, and takes the account-scoped advisory transaction lock.
All original-document hashes/current broker bindings are rechecked by the existing
input service and publication engine. Stale/foreign sources, VIEW access, disabled
users, archived accounts, incomplete evidence and closing differences cannot publish.

The publisher reuses the existing reconciliation/import engine and source receipt.
It permits initial publication and sequential months, preserving published opening
state. Existing months, historical backfill and missing predecessor months remain
blocked. All canonical artifacts, the visibility marker and the source receipt
share one SQL transaction; a failure must roll back all of them. Handles expire
at the end and caught SQL errors cannot count as a successful transaction.

A new append-only adapter uses plain INSERT, never UPSERT. The database publisher
role has SELECT/INSERT on artifacts, with neither UPDATE nor DELETE. An engine
request to remove a visibility/optional key is a no-op only when absent; an existing
key blocks publication. Unique storage keys provide an additional overwrite guard.
The result is deployment-specific and does not imply approval to publish production.

Version 3 records a separately reviewed publisher-role policy/catalog transition.
The ordinary input-role grants are unchanged. Publisher metadata access retains
the directory/audit checks; draft access allows SELECT/UPDATE for locking, original
documents are SELECT-only, preferences remain unavailable, and all three retained
public tables stay SELECT-only. Elevated roles, memberships, ownership, schema
CREATE, unexpected grants and grant options remain prohibited. The version 1 SQL
and version 2 policy checksums remain unchanged; version 3 verifies the latest
schema/table/column ACL signature. No default or PUBLIC grants are changed.

Run the normal tests followed by:

```
PYTHONPATH=src python -m campaigniq.account_runtime_roles_admin --development-only --publication
```

The existing sudo launcher runs this fixed new rehearsal as the existing local
postgres OS account. It stages only Python source, driver dependencies and the four
named tracked synthetic month fixtures. No private fixtures/configuration,
credentials or actual brokerage documents are copied. Reports are private and
owned by the normal user. The new job remains pinned to the local full restore.

The full-restore rehearsal creates both NOLOGIN roles, applies reviewed input and
publication grants, bootstraps a new synthetic directory and publishes synthetic
January/February only within one forced-rollback transaction. It tests grant
rollback, exact review, confirmation/revision/owner/VIEW/archive checks, closing
mismatch, injected failure after the visibility marker, a 110-share January and
115-share February, source preservation, duplicate-month/backfill/gap rejection,
other-account isolation, ordinary-input write denial and publisher rewrite/delete
and original-document/legacy write denial. A final independent read-only connection
checks original rows and permission signatures and absence of both roles/schema.

This rehearsal uses real restricted SESSION AUTHORIZATION and the real SQL
adapters/engine, with a test-only connection factory routing to the same local
rollback transaction. No remote wrapper/TLS guard is relaxed. It does not claim
committed independent-connection concurrency, live deployment TLS or dashboard
activation parity; those remain follow-up integration checks before a release.
Normal runtime connection factories retain operation-scoped commit/rollback,
account serialization and permission rechecks. Code rollback must preserve any
future committed account tables and post-activation user records.
