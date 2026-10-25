# Administration and brokerage accounts: development foundation

Status: offline policy only. No registry table, runtime switch, account selector,
or production migration is included in this increment.

## Existing behavior verified in the supplied deployed source

`access.py` maps one authenticated subject to one trusted workspace. The OIDC
adapter reads `sub`; authorization comes from a server environment mapping.
`runtime.py` uses the workspace as both the PostgreSQL artifact namespace and
the filesystem workspace directory. Import drafts are also workspace-scoped.
Consequently, an account selector alone would be insufficient isolation.

## Foundation decisions

- Users have stable identifiers separate from display names. Identity keys pair
  a trusted issuer with a subject. They must come from authenticated server-side
  OIDC validation, never a browser form or an email/name match.
- Each brokerage account has an immutable UUID, an owner, and a storage scope.
  Names and broker labels are descriptive; two accounts may share a name.
- An explicit offline legacy proposal retains each existing workspace scope
  byte-for-byte as the owner's initial account binding. It does not read, copy,
  relabel, or migrate financial records. Ambiguous ownership requires review.
- New accounts receive distinct `account-<UUID hex>` scopes. These are proposed
  storage bindings, not new physical databases or activated runtime namespaces.
- Owners can read/edit their accounts. Explicit sharing grants allow view or
  edit. Administrator status alone does not grant financial-record access.
- Administrators add users/accounts, configure users, rename/archive accounts,
  and grant/revoke access. User removal initially means disabling access while
  retaining ownership, grants, audit history and original financial evidence.
- Disabled users cannot access owned or shared accounts. Archived accounts are
  readable but not writable. The last enabled administrator cannot be disabled
  or demoted. Existing grants are retained when a user is disabled; re-enabling
  restores them, so the eventual admin screen must show that explicitly.
- Report selections authorize every selected account and retain separate account
  identifiers/scopes. This increment does not calculate consolidated performance
  or merge campaigns, currencies, transfers or brokerage settlements.
- Pure commands return new directory snapshots and activity events. Invalid
  commands leave their inputs unchanged. Expected revisions reject commands
  against the supplied newer snapshot. These checks do NOT provide durable
  audit storage, session revocation or concurrent database compare-and-swap.
- Password recovery stays with the external sign-in provider; CampaignIQ will
  manage application access rather than store or reset Google passwords.

## Required before activation

1. Persist directory records, grants, unique bindings and audit events in the
   existing PostgreSQL database. Commit authorization changes, revision checks
   and audit events atomically; recheck enabled users/permissions on each request.
   Bootstrap the first admin from an explicit trusted existing identity.
2. Pass only authorized account contexts into artifact storage, drafts/documents,
   publication, cache keys, source histories, preferences and analytics. Scope
   campaign/lot/settlement identities by account wherever data is combined.
3. Add admin and account-selector UI backed by that service boundary; show
   archived/disabled status and access-restoration effects. Do not expose raw
   directory data or database credentials to browser account selectors.
4. Rehearse a verified existing-data compatibility plan, including Dennis and
   Andrew's records. Match artifacts, original documents, drafts, campaign
   identities and reports before any production activation. No data re-entry.
5. Add consolidated reporting with explicit currency handling and account-aware
   transfer/duplicate controls. Never add rate-of-return percentages together.

This additive installer changes no existing application files and activates none
of these future integration steps. Production changes require separate approval.
