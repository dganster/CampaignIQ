# Futures generation writer and disposable database rehearsal

The internal writer uses an already authorized, account-serialized publication
transaction. The public deployment publisher still rejects futures months. This
checkpoint does not activate production futures publication.

Before any financial INSERT, the writer checks the current draft revision and all
monthly securities, Forex, futures and crypto controls. Futures sources and owner
confirmations are rebuilt and validated. Active crypto cannot be omitted by a market
checkbox; its missing statement or unresolved cash controls block generation.

The ordinary accounting engine runs against an in-memory generation buffer, with a
temporary securities projection of the verified original Trade History. Only verified
futures execution rows are removed. The source original remains unchanged, and the
published import provenance points to its exact bytes. The separate futures record
contains the original/derived fingerprints and removed source row identifiers.
Crypto generation, when applicable, is rebuilt from the full original export so its
source row identifiers are not reindexed by the securities projection.

The buffer combines the ordinary financial artifacts, futures record and extended
source receipt. Canonical account result and dashboard readers verify it before
any rows are inserted. Every INSERT uses the same restricted-role SQL transaction;
the final-month marker is inserted last. A caught SQL failure poisons the transaction
and cannot commit a partial month. Existing months cannot be replaced.

Dashboard snapshots now retain the publication protocol header alongside selected
published records. This lets strict component readers apply the same finalization
boundary as the account reader. Historical original-document provenance continues
to reference the full saved upload.

## Local rehearsal

Run the existing administrator launcher with --development-only --futures-publication.
It targets only campaigniq_restore_20261024_194114 through the fixed local Unix socket,
clones it into a newly generated disposable database and uses separate temporary
restricted input/publication roles. It never connects to production. The retained
restore is read only. All new financial writes are synthetic and occur in the clone.

The futures extension checks real PDF extraction, opening/ending owner confirmations,
input identity, rollback after component/receipt/marker writes and caught forbidden
SQL, simultaneous publication, independent uncommitted reads, committed canonical
results, dashboard totals, original history fingerprint, next-month opening state and
corrupt-record rejection. The existing committed-publication checks also run. Cleanup
requires the exact created database and role OIDs and unchanged copied source rows;
there is no FORCE or CASCADE. Receipts are private. Failed checks preserve reports.

Ordinary unit tests use synthetic storage and a real synthetic PDF. They do not
stand in for the restricted PostgreSQL rehearsal, which must pass on the local host
before the public futures path can be considered for activation. Crypto statement
support/reconciliation and a reviewed deployment remain separate unfinished work.
