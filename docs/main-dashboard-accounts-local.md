# Local account-aware main dashboard

The existing local signed-in launcher now opens the main CampaignIQ report
pages with one authorized brokerage-account selector. Production entry points
and local database routing guards remain unchanged.

The selected account supplies Overview, Campaigns, Performance, Positions and
Crypto. Data uses the tested signed-in input/publication services for new
accounts, or read-only retained-data inspection for existing accounts.
Administration appears only for a currently enabled administrator; directory
commands still recheck authorization and revision under their existing lock.
Compare accounts retains its explicit selection and opt-in quantity total.

Each report request reauthorizes the server-owned Google session. Legacy bytes
come from the exact retained namespace and are compared with direct source
rows in the same read-only repeatable snapshot. New account sources use a
separate read-only repeatable snapshot under the existing directory lock.
Every visible new generation must have a finalized marker, matching draft,
source receipt, original-document fingerprints and matching market/period
artifacts. All live handles expire before rendering. No financial report
snapshot is cached in process globals or browser session state.

Rendering uses a private instance of the existing main dashboard module per
request, never changes a shared imported module's storage globals, and never
initializes the legacy runtime. Widgets, filters, visit-local routes and
preference choices are namespaced by registry, signed-in user and account.
Existing saved display preferences are read when present; changes apply only
for the visit and do not write to financial artifact storage. Production
preferences retain their existing behavior. A later dedicated preference
store is required for durable changes in this new local composition.

Supplementary filesystem trade-history CSV evidence has not been attached to
the scoped reader. The UI states this limitation, and report helpers receive
an empty temporary history location rather than another workspace's or the
developer's files. Published record analytics remain available; unsupported
history classifications retain the existing incomplete/unclassified behavior.
Browser URL sharing/back-forward restoration is not enabled for scoped reports;
report routes remain account-specific visit state.

The Performance page also handles unavailable campaign win rate as an em dash
instead of attempting float(None), which affected accounts with no closed
campaigns. It does not infer a zero win rate.

Run full tests and then the local account_dashboard_rehearsal. The rehearsal
creates two NEW synthetic local accounts, publishes 110/220-share fixtures,
checks account isolation, read-only repeatable snapshots, injected artifact,
draft and SQL writes, handle expiry, current grants/revocation/archiving,
receipt corruption, and unchanged prior financial rows and registries.
It never connects to production. Synthetic rows remain for investigation.

The installer verifies the exact 1dfbcfa source baseline, backs up changed
files, and carries forward the verified recovery source retained by the
Dashboard Foundation installer. Earlier publication packets and recovery
receipts are preserved. Installation and preview startup do not deploy code
or migrate production data.
