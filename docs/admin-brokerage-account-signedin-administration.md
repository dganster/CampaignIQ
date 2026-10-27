# Signed-in local administration

The existing local signed-in preview now includes an administration expander for
enabled administrators, using the current server-owned OIDC session. There is
no demo actor selector. Members receive no directory or administration widgets.
An administrator who has no readable legacy accounts can still administer the
directory; administration is rendered before the financial account selector.

Changes go through the existing guarded LOCAL directory service, which reloads
the current actor under its update lock, checks expected revision, and commits
state and audit together. Each event records the signed-in user's ID, not a
caller-supplied actor. Cached form revisions prevent stale overwrite; Reload
directory refreshes forms. The last enabled administrator remains protected.

Controls cover user display names/enabled/admin status, registration of a new
independently verified exact Google subject under the fixed configured issuer,
account names/archive status, new account metadata, grants/revocation and audit
history. No email lookup or implicit enrollment is added. Registration requires
explicit administrator identity verification; this tool does not independently
prove that a manually supplied Google subject belongs to a particular person.
Original workspace owners and storage bindings cannot be edited through these
forms. Disabled users retain ownership and grants but cannot access the service.

New accounts are metadata only and are marked as awaiting financial activation.
Edit grants are recorded by policy but do not enable financial writes in this
read-only application. No account deletion, document changes, imports, financial
publication or production operation is exposed. Administrator status does not
grant financial access to other owners' accounts.

The same saved local configuration and local Google OAuth client are reused.
The installer changes only the development signed-in preview integration and
adds service, view, rehearsal, tests and this document. It backs up the exact
previous integration source. Original deployed runtime files remain unchanged.
The separate rehearsal uses a new synthetic registry, tests stale/unauthorized
commands and state/audit rollback, and verifies all prior financial rows and
other registries remain unchanged. It never changes the real local directory.

After rehearsal, restart the preview with the existing --resume configuration.
Test a harmless local display-name or account-name change and verify it appears
in Activity. No production deployment is authorized or performed by this work.
