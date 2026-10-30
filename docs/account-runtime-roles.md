# Deployment runtime database grants

Baseline: 22e0ead. This increment prepares the ordinary directory and monthly
input role only. It does not activate the deployment backend, create a production
role, configure credentials, connect to Render, or grant publication access.
Existing LOCAL adapters and launchers are unchanged.

`account_runtime_roles` has no connection factory or command-line writer.
The caller provides an active transaction and an explicitly named existing role.
The read-only proposal pins its role OID, database, current catalog signature,
effective existing grants and policy checksum. Applying grants requires that
exact proposal checksum. A replaced role, changed catalog or changed permissions
invalidates the proposal. The caller owns commit/rollback and must propagate
errors. This module never creates roles, changes passwords, revokes permissions,
changes ownership or silently repairs drift.

The role cannot have elevated flags, membership in another role, database/schema
CREATE, ownership of account/retained storage, unexpected table or column
privileges, or grant options. Inherited PUBLIC privileges are checked as effective
permissions and can block setup; this package never changes PUBLIC grants.

Allowed grants:

| Tables | Privileges |
| --- | --- |
| account directories | SELECT, UPDATE |
| account audit events | SELECT, INSERT |
| account migration ledger | SELECT |
| account drafts and original document versions | SELECT, INSERT, UPDATE |
| account published artifacts | SELECT |
| retained public artifacts, drafts and original versions | SELECT |
| account preferences | None in this increment |

Only USAGE is granted on the two required schemas. There are no default grants
for future tables. Publication and preferences need their own later reviewed
policies; they are not enabled here. Application authorization remains necessary:
this database role belongs to the trusted server, not individual end users.
Owner/account checks, current server sign-in, grants/revocation and audited
transactions remain in the existing service code.

Version 1 DDL and its source checksum are unchanged. Version 2 appends a policy
checksum and the resulting catalog signature to the owner-only migration ledger.
The existing schema verifier accepts either the exact original version 1 state
or the exact version 1 + version 2 transition. A repeat validates without grants;
unknown versions, changed policy checksums or schema/table/column permission drift block access.

Run locally:

```
PYTHONPATH=src python -m campaigniq.account_runtime_roles_rehearsal --development-only
```

The rehearsal targets only campaigniq_restore_20261024_194114 on the local Unix
socket as the current OS user. It refuses an existing deployment schema and
requires that restore role to be a superuser, solely to test PostgreSQL SESSION
AUTHORIZATION with an actual restricted identity. It will stop before mutations
if that prerequisite is absent. It never asks for new privileges or credentials.

Within one forced-rollback transaction it creates a unique NOLOGIN test role and
new schema, proves injected grant failure rollback, rejects stale review, applies
and repeats versioned grants, then executes allowed and denied SQL under the
restricted identity. Both existing runtime validators run unchanged. Tests deny
legacy writes, artifact writes, audit rewrites, ledger writes, table creation,
preferences access and truncation. Privilege drift is rejected without repair.
Original tables remain SHARE locked. The final read-only observer confirms exact
original rows and retained permission signatures, plus absence of the temporary role and schema after rollback. There
is no separate cleanup writer or committed synthetic database state.

The user runs the actual PostgreSQL rehearsal on Jumbo; container unit checks do
not claim to verify remote TLS or Render role provisioning. Production role
provisioning, publication privileges, server activation, release review and
production approval remain separate work.

## Local administrator launcher for an ordinary restore owner

If the normal OS/database user is not a superuser, keep its permissions unchanged
and run:

```
PYTHONPATH=src python -m campaigniq.account_runtime_roles_admin --development-only
```

This launcher uses the existing local `postgres` OS account through
`sudo -u postgres`. Sudo may ask for the Linux user's computer password at its
normal terminal prompt; the program never receives or stores that password.
There is no new database superuser or password, no role changes for dganster,
and no weakening of peer authentication, home-directory permissions, production
routing guards or application connection checks. If sudo is unavailable or not
authorized, it stops rather than requesting new database permissions.

The normal user creates a private temporary bundle containing only campaigniq
Python source and installed PostgreSQL binary-driver dependencies. No credentials,
private fixtures, documents, configuration files or home directories are copied.
The base system Python runs with `-I` as postgres. Its stdlib bootstrap verifies
Python compatibility, archive paths, file inventory and checksums before importing
code from a separate private postgres-owned temporary directory. The unchanged
fixed-target rehearsal connects as postgres only to the local full restore.
Every schema, role and grant change remains inside the forced-rollback transaction.

JSON receipts are streamed back and saved with mode 0600 by the normal user in a
mode 0700 Development-Rehearsals folder. A durable request receipt identifies the
fixed target, temporary bundle checksum and rollback policy. Temporary source and
driver copies are removed after execution. These temporary filesystem removals
never target user project files, records or database backups. A failed run does
not count as success; available reports are preserved. No production database,
server launch, account activation or role provisioning is introduced.
