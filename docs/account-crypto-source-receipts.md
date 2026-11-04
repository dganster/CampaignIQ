# Account-scoped crypto originals and source-choice receipts

The development application now composes a guarded source review, owner save and
read-only saved preview. These services do not write financial artifacts or enable
publication. No schema, database grant or live configuration changes are required.

## Review and save

review_crypto_statements reads the selected account's current saved Trade History,
verifies its full securities identity against the current binding, and calculates
the documented statement-backed preview from the previous and current PDF originals.
It returns the exact preview digest and displayed draft revision. This is a read-only
review, not an acceptance or a broker-account binding operation.

save_crypto_statement_sources requires that exact reviewed digest, current revision,
literal confirmations that both statements belong to the selected crypto account and
that the displayed source choices are accepted. It reauthorizes the current server
session, requires account ownership, rejects published months and recomputes the
review from the current originals inside the same serialized input transaction.

The previous PDF is stored as crypto_previous_statement_pdf_v1, the current PDF as
schwab_crypto_statement and the metadata receipt as crypto_statement_sources_approval_v1.
The receipt binds account, draft, month, server actor, timezone-aware timestamp,
source revision, exact three source fingerprints, current securities binding digest,
full crypto identifier digest, policy identifier, preview digest and discrepancy
review digest. Raw full account numbers are not copied into the receipt.

The Trade History must supply the full separate crypto identifier. The matching PDF
suffix supplements that context; it is not treated as full broker identity. The owner
association is labeled as owner-confirmed, with broker_verified_account_link=false.
It grants no account access and does not create or change directory bindings.

Exact repeated saves add no revision or duplicate originals. Source replacements
require a fresh exact review and explicit replacement confirmation; prior originals
and the receipt are preserved by the existing versioned document repository. Missing,
orphaned or corrupt approved originals/receipts cannot be adopted or repaired by this
API. A changed crypto identifier or securities binding cannot be silently relinked.
All three writes and post-write source verification share one transaction.

A current statement already saved through the ordinary upload role may be confirmed
after the same fresh, exact-byte review and owner checks. A different replacement
requires explicit confirmation and preserves the previous upload. This does not
permit adoption of inconsistent previous-statement metadata or a corrupt approval.

## Saved preview

crypto_statement_preview uses current account authorization, revision and a read-only
financial snapshot. It reloads both exact PDFs, the receipt and the saved Trade History,
then recomputes the entire calculation. Changed sources, current binding or calculation
withhold the result. A current view grantee may read; an edit grant does not allow
another person to approve the owner's source choices. Revocation remains authoritative.

The returned source_receipt_verified flag is distinct from the pure calculation's
default approval flags. The pure calculation cannot manufacture account approval;
the account-scoped receipt supplies that separate evidence. Both return
publication_authorized=false and financial_state_written=false.

The user has already approved the accounting treatment during development. The
eventual user-facing confirmation records that acceptance against their signed-in
account and exact uploaded documents. It is not a request to reconsider the same
accounting choice. Browser controls, monthly crypto generation and combined publication
integration remain separate work. Existing strict import and futures publication
guards are unchanged by this checkpoint.

## Disposable local PostgreSQL rehearsal

The fixed administrator launcher supports --development-only --crypto-source-receipts.
It clones only campaigniq_restore_20261024_194114 on the local Unix socket into a new
generated disposable database. Existing committed-publication checks run first, then
synthetic crypto receipt checks use the actual restricted input role. No production
connection or persistent role elevation is made.

The extension exercises read-only reviews, stale/foreign/changed-review rejection,
failure after either PDF and receipt, caught forbidden SQL, concurrent first approval,
exact-repeat idempotence, independent saved-source verification, current grant checks,
owner-only approval, database read-only enforcement and changed-original rejection.
Existing financial artifacts stay byte-identical. Cleanup reuses exact new database
and role OID checks with unchanged copied source rows, without FORCE or CASCADE.
Private receipts identify failures and completed cleanup. Failed runs retain reports.
