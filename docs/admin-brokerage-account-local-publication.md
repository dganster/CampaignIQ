# Synthetic local account publication rehearsal

This increment adds a server-only publication rehearsal API and runner. No
browser publish button, production service, schema or live runtime is changed.
The real local PostgreSQL gateway still rejects Render, remote databases,
non-campaigniq_dev routes, nonlocal socket/OS roles and nonsynthetic registries.
Each invocation requires authorize_synthetic_publication=True. The rehearsal
passes it explicitly for new synthetic accounts only.

The write transaction rechecks enabled user, edit permission, archive status,
account binding, draft ownership and displayed revision. A transaction advisory
lock serializes this publisher per physical account scope, before the draft row
is locked. The existing assess_backfill guard rejects existing or later months.
Target-month artifacts cannot be replaced, orphaned storage is not adopted, and
continuation requires the exact preceding published month. These restrictions
make this an append-only local rehearsal, not a backfill or repair workflow.

It restores original draft bytes, stages private temporary parser inputs,
verifies broker identity, and runs the existing prepare_monthly_import and full
execute_monthly_import functions. No reconciliation exception, historical
reseeding or external source archive is passed. Every artifact, protocol and
visibility marker uses the account session's single connection. A mismatch or
source-control failure raises and rolls the transaction back. After the engine
stages a complete generation, an account-specific source receipt records actor,
registry/account/draft IDs, revision and original document hashes/sizes. It
contains no credentials or full broker account numbers. The outer SQL context
must commit before the result reaches the caller. A commit error is propagated;
no automatic retry or deletion is attempted.

Original draft documents and versions remain in PostgreSQL. Parser files are
removed after success or failure; durable provenance records content hashes,
not temporary paths. The receipt links original document bytes while the existing
pipeline provenance fingerprints its exact staged parser inputs. No existing
published generation is overwritten. There is no production migration or
publication authorization in this API, and no browser route to invoke it.

The SQL runner creates a new synthetic account, exercises first publication
with the existing financial engine, injects a failure immediately after its
marker is staged and checks exact rollback across all data rows. Two concurrent
duplicate attempts must have one winner. An independent read checks the complete
committed generation, receipt, preserved source bytes and 110-share January lot
state. Duplicate replacement is blocked, other accounts remain empty, and view
grants cannot publish. February supplies no opening snapshot: it must consume
January's stored authoritative state, finish at 115 shares, and retain every
January artifact unchanged. March deliberately mismatches; earlier backfill and
a skipped-month attempt are blocked. Existing financial tables and earlier
synthetic records are compared before/after. The two committed synthetic months
and their original source drafts are retained for inspection.

This verifies a simple equities-only January/February fixture. It does not prove
all broker/market formats, full financial correctness for real accounts,
production identity provisioning, legacy-data migration, or production cutover
and recovery. Current crypto/masked-account limitations remain. The existing
browser remains a read-only preview/reconciliation/comparison tool.

Run the full suite, then:

    PYTHONPATH=src python -m campaigniq.account_publication_rehearsal --development-only

The runner publishes only within its NEW synthetic local account. All production
connections and production publication remain disabled.
