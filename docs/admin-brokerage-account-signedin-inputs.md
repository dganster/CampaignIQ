# Saved monthly inputs for new accounts under local real sign-in

The signed-in preview now exposes a separate monthly-input panel for NEW
brokerage accounts. It retains existing workspace access on the prior read-only
reader. The new gateway rejects legacy accounts rather than copying or moving
their financial data. No production runtime or synthetic gateway is modified.
Monthly uploads currently accept only Schwab accounts (broker label `schwab` or
`Charles Schwab`); other brokers remain metadata-only and are rejected by this gateway.

An administrator explicitly initializes `campaigniq_signedin_accounts_dev`.
Artifacts, drafts and original documents use separate tables in this schema
and a physical namespace combining registry and account UUIDs. Missing tables
fail closed; there is no fallback to public workspace or synthetic tables.
Initialization creates empty tables only. Account creation remains an audited
directory action, and accounts are visible only through ownership or grants.

Every data transaction consumes the actual server-owned OIDC session, checks
exact issuer/subject and enabled user, validates directory/audit and legacy
bindings, and authorizes the account again. A directory SHARE lock is retained
through the transaction. Revocation, disable/archive and binding commands wait
for active transactions and block later unauthorized access. Ended handles and
caught SQL failures cannot silently commit further operations. No browser actor
or workspace parameter is trusted.

The bound monthly-input service reuses existing role/type/size rules, exact
broker header identity checks, draft scoping/revisions, replacement confirmation,
original-version retention and monthly input preflight. A new explicitly guarded
subclass accepts only the new gateway; the old synthetic constructor/publication
guards are unchanged. Financial artifact mutations are denied even during upload
transactions. There is no publication or reconciliation endpoint in this panel.

Broker binding is an administrator action attributed to the signed-in identity,
with directory revision CAS and atomic audit. Only a digest and last four digits
are retained in the binding event. Rebinding invalidates old document identity
on the next preflight without deleting any bytes. Duplicate identifiers are
blocked among known bindings in this registry; unbound legacy identifiers cannot
be compared by this gate. Broker ownership is not independently inferred from
names, emails or account labels. Masked-only crypto evidence is still unsupported.

UI state is partitioned by registry, signed-in user, account, draft and revision.
View grants and archived accounts allow saved-input previews but no uploads.
An Edit grant still does not enable financial artifact mutation or publication.
The existing real sign-in configuration/client is reused; no new credentials are
needed. Existing monthly data does not need re-entry. This does not activate
new-account reporting, account aggregation, full financial continuity or publish.

The local rehearsal uses a NEW synthetic directory and new accounts only. It
tests the real-sign-in service path with explicit fake server-session objects,
not an external provider callback. It checks source isolation, bound uploads,
original retention, preflight immutability, rollback, artifact-write rejection,
revocation locking and prior-row preservation. No real local directory is changed
by the rehearsal. Browser verification under actual Google sign-in remains a
separate step after the PostgreSQL rehearsal passes.
