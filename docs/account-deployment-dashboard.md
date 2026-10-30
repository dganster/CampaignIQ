# Deployment dashboard and new-account comparison parity

Baseline c07c032. This increment connects detached dashboard snapshots and account
comparison reads to the verified deployment backend. It does not switch the running
UI, deploy code, connect to Render, migrate preferences or activate production.
Existing LOCAL dashboard services and guards remain unchanged.

`DeploymentAccountDashboard` accepts only the explicit deployment directory. Its
choices use fresh server sign-in and audited directory authorization; administrator
status grants no implicit financial access. Snapshot selection is authorized again
in a guarded read-only financial transaction. No prior overview or selected UI ID
is a reusable capability.

Retained accounts use the exact public workspace namespace, compare every adapter
byte with the direct source rows, and validate all visible canonical periods.
New accounts require matching saved source drafts, publication markers and receipts,
all original-version checksums, current broker identity, canonical periods and market
choices. An orphan generation or conflicting receipt blocks the snapshot. Returned
ReportSnapshot objects contain detached immutable source text and reject mutation;
no database handle escapes. The existing load_dashboard_sources path consumes them
without rewriting financial data or recomputing publication.

Optional history is bound in the same financial snapshot to selected-account saved
originals and published provenance hashes. Dedicated deployment history queries use
only campaigniq_accounts, while retained history uses only public. All saved versions
are eligible to prove published history; other namespaces and shared developer folders
are never fallback evidence. Missing retained originals remain explicit coverage gaps.
The four tracked synthetic fixtures are the only test evidence copied into staging.

`compare_deployment_accounts` reuses the existing PublishedSelection and result models
for saved new-account months. It validates the complete selection, current server
permissions, saved draft IDs/revisions and reporting month in one READ ONLY REPEATABLE
READ snapshot under the authorization SHARE lock. All handles expire on exit. Duplicate,
foreign, stale, invalid or unauthorized entries fail the whole request, with no partial
report or combined total. Accounts retain separate results. Combined position quantities
require the explicit boolean opt-in; financial subtotals are not combined by this API.
Retained-account dashboard/canonical parity remains separate from this new-account
comparison contract, preserving unknown market coverage and saved monetary values.

The single-account deployment reader now shares its existing session-bound validation
helper with dashboard/comparison code. Its public type, revision, sign-in, source and
read-only rules are unchanged.

Run normal pytest followed by:

```
PYTHONPATH=src python -m campaigniq.account_runtime_roles_admin --development-only --dashboard-parity
```

The fixed sudo job extends the successful disposable-clone committed-publication
rehearsal. It repeats all twelve existing checks, then publishes a second synthetic
new account at 220 shares and compares it with the 110-share account. It checks the
following before clone cleanup:

- Existing dashboard analytics and every selected new-account artifact match independent
  source rows; current original history covers two months for account A and one for B.
- Every retained owner preserves exact workspace bytes, existing analytics and explicit
  retained-history coverage, with no writes or cross-owner administrator access.
- Each new-account comparison opens exactly one read-only data snapshot; 110 and 220
  stay separate and a combined 330 requires opt-in.
- Duplicate/stale/foreign/unauthorized selections return no partial comparison or totals.
- A separately committed source-receipt change between account reads cannot mix snapshot
  generations. A fresh request then withholds both comparison and conflicting dashboard.
  Fault injection is confined to new synthetic data in the disposable clone.
- Whole-selection revocation, readonly archived history and disabled actors are rechecked.

The clone and role identity checks, unchanged copied original rows, dependency-restricted
cleanup and final independent verification of the retained restore remain in place.
Reports are saved privately under the normal user. Existing project files, original
records and backups are not cleanup targets. If cleanup identity checks fail, resources
and receipts remain available for investigation.

Deployment preference storage, service composition/activation, live TLS verification,
release review and production approval are still separate work. This package introduces
no environment-discovered credentials, runtime activation flag, commit or push.
