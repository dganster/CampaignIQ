# Forex cash source-save PostgreSQL rehearsal

Run the existing administrator launcher with `--development-only --forex-cash-source-receipts`.
The exact source checkpoint is installed and its full suite must pass first. The launcher
stages checksum-verified Python code, installed binary driver dependencies and the existing
public fixtures, then uses the existing local postgres OS account. No credentials or user
source documents are packaged. No arbitrary database/host argument is accepted.

The fixed target is a new disposable clone of `campaigniq_restore_20261024_194114` on
`/var/run/postgresql`, port 5432. The retained restore is read only. The common committed
publication harness exercises synthetic securities baseline checks; the new Forex extension
saves source documents and receipts only, through the actual restricted input role. It
introduces no Forex financial writer, accounting approval, reconciliation exception, source
resolution, runtime configuration or production activation.

The extension checks real CSV parsing/account headers, exact unchanged saved history/report,
owner attribution, original source fingerprints, monetary discrepancy retention, rollback after
each original and receipt INSERT, rollback after caught forbidden SQL, concurrent one-winner
save and exact-repeat no-op, invisible partial saves and repeatable readers, stale revisions,
foreign accounts, view/edit/archive/disabled/revoked access, read-only SQL enforcement,
corrupt originals/receipts rejected without repair, and explicit replacement with retained
superseded originals/receipts. Existing financial artifacts must remain identical across the
Forex extension. The common harness also checks every copied original table and retained
restore/catalog, then removes only exact generated database/role identities without FORCE,
CASCADE or DROP OWNED. Private receipts are returned to the normal user.

The database-dependent test is opt-in (`CAMPAIGNIQ_FOREX_CASH_SOURCES_TEST=1`) and requires
the fixed local restore and postgres OS identity. A skipped database test is not evidence that
this rehearsal passed. The installer automatically invokes the guarded administrator launcher
after the ordinary full suite; it reports success only when the complete database receipts pass.
If any check fails, preserve reports and installed code and do not commit until reviewed.

Next work remains authoritative Forex opening/closing validation and financial candidate/writer
integration. Neither this checkpoint nor a passing source-save rehearsal authorizes publication.
