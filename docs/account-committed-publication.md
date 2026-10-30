# Independent committed publication rehearsal

Baseline 8bf8e3c. This increment adds the deployment canonical results reader and
verifies publication with independent committed PostgreSQL connections. No UI
switch, Render connection, production deployment, account activation or permanent
role provisioning is introduced. Existing local publisher guards remain unchanged.

The new reader requires the exact deployment input service, current server sign-in
and displayed source revision. The gateway holds authorization under its directory
SHARE lock, while all originals, broker binding checks, publication marker/receipt,
positions and monetary artifacts use one READ ONLY REPEATABLE READ transaction.
The existing canonical results logic is reused with an explicit deployment receipt
format; the old signed-in local reader retains its original format default. Original
versions and checksums, current broker identity, source receipt, market choices and
periods must agree before any result is returned. No reader repair is performed.

Run normal pytest, then:

```
PYTHONPATH=src python -m campaigniq.account_runtime_roles_admin --development-only --committed-publication
```

The existing sudo launcher runs the fixed new job as the existing local postgres
OS account. It stages code, PostgreSQL driver dependencies and only the same four
named tracked synthetic fixtures. It does not copy credentials, private test inputs
or actual brokerage documents into filesystem staging. Reports remain private and
owned by the normal user; dganster's privileges remain unchanged.

Unlike the prior rollback rehearsal, this job creates a uniquely named disposable
LOCAL database from campaigniq_restore_20261024_194114. Every source connection
closes before CREATE DATABASE TEMPLATE; an active source session can block creation
and is never terminated by the job. The retained restore receives only reads and
is independently fingerprinted before/after, including its retained permissions.
There is no need to change source database role grants or original source rows.

The clone is postgres-owned and PUBLIC CONNECT is revoked. Two unique NOLOGIN
roles receive the reviewed input/publication policies only in the clone. No normal
user gets an administrator grant or new login credentials. Connections authenticate
locally as postgres, validate the generated clone target, then SET LOCAL SESSION
AUTHORIZATION to the restricted role and run the existing runtime/SQL permission
validators. Operation commits, read-only transactions, authorization locks, advisory
publication locks and failed-transaction checks remain real. Only the test connection
factory is substituted; no deployed endpoint or TLS guard is relaxed.

Tests commit source drafts, then inject a post-marker failure and independently
compare every account table before/after rollback. Two simultaneous independent
publishers attempt the same January. One must commit; the other must reject the
now-published month. While a marker write is uncommitted, a third read-only repeatable
snapshot must see no partial generation and remain unchanged after the winner
commits. A fresh canonical reader must verify 110 shares and the original receipt.
Additional checks cover raw SQL read-only enforcement/expired handles, cross-account
isolation, foreign actors/drafts, committed February using January opening state
(115 shares), byte-identical January results, view revocation waiting for an active
reader then denying later access, and an independently committed receipt conflict
withholding the entire result without reader repair. Corruption is injected only
into a new synthetic account in the disposable clone and restored there for checking.

Cleanup is limited to the generated database name, its recorded creation OID and
postgres ownership, plus exactly matching temporary role OIDs/names. Copied original
table fingerprints must match the retained source before cleanup. Unknown identity,
changed copied original rows or an active unexpected clone connection blocks cleanup
and preserves resources/reports for investigation. DROP DATABASE has no FORCE;
DROP ROLE uses dependency checks, with no DROP OWNED, CASCADE or automatic cleanup
of unrelated grants. Original project files, backups and the retained restore are
never deletion targets. A final independent observer verifies the unchanged retained
restore and absence of the clone and both temporary roles.

The committed source/role resources receipt supports recovery if the process stops.
A failure is never reported as a successful rehearsal. Production activation, live
TLS verification, deployment dashboard/comparison parity and release review remain
separate work. No commit, push, migration or publication is performed in production.
