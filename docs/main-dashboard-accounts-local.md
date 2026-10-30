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
Personal display preferences now persist in the separate LOCAL table
campaigniq_preferences_dev.account_preferences, keyed by registry, signed-in
user and brokerage account. Existing financial-table preference bytes are read
as a fallback and never copied, overwritten or removed. Other financial data
and production preferences retain their existing behavior. Schema activation
is an explicit administrator operation (the local rehearsal creates it for its
new synthetic registry). Until activated, controls remain visit-local.

Preference reads use a read-only repeatable snapshot. Saves hold a directory
SHARE lock, recheck server sign-in and account access, serialize each preference
key with an advisory transaction lock and reject stale revisions. Read-only
financial grants may save personal display choices, including on archived
accounts; disabled or revoked users cannot. A stored document checksum and
strict value validation prevent overwriting corrupt preference rows; the UI
keeps report access available with visit-local preferences in that case. Display
preference changes do not modify the account directory or its audit ledger.
The Reload saved preferences control recovers from another tab's newer save.

Supplementary trade-history evidence now comes from the selected account's
saved original documents, read in the same read-only repeatable snapshot as
its published artifacts. The exact byte size and SHA256 must match that
month's published Thinkorswim input provenance and the original's stored hash.
A current securities broker binding, when present, is rechecked. A retained
superseded document or a document in an abandoned draft may supply the exact
published original; an unpublished replacement is never substituted.

Only matching originals are staged, without changing their bytes, in a private
temporary directory under canonical month filenames. Uploaded filenames are
never used as paths. The existing journal/source, roll and strategy readers
still apply their own provenance and campaign checks. The directory and files
are deleted when rendering ends, including Streamlit stop/rerun paths. There
is no shared-folder search and no filesystem fallback to another account.

The UI reports how many published months have matching retained originals.
Unavailable, changed or unbound originals leave a supplementary-history gap;
financial records and published journals are not rewritten. Existing validation
of published new-account source receipts and originals stays active. Missing original
bytes are not reconstructed from totals. Original documents, all prior
versions and financial artifacts remain unchanged. Browser URL sharing and
back-forward restoration remain outside this scoped composition; reporting
routes stay account-specific visit state.

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
