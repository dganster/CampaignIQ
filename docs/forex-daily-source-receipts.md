# Durable daily Forex PDF originals and owner receipts

The existing daily inventory review can now save the original PDFs with an owner-attributed
receipt. This source-only checkpoint does not alter financial publication, monetary resolution,
account bindings, database schema, grants, credentials or runtime activation.

The ZIP is an upload container. Each exact original PDF is stored separately using the existing
PDF document repository and a date-scoped role. The receipt retains its original path in the
archive, exact PDF SHA256, stored document identity, and the original ZIP SHA256. The ZIP bytes
are not stored or reconstructed; the broker PDF originals are retained byte-for-byte. No general
ZIP MIME support or new blob storage is introduced.

Saving requires the current displayed revision, a newly rebuilt review fingerprint, current owner
identity, explicit account association and acceptance of the displayed daily coverage limits.
The existing account write transaction locks the draft, rechecks the published-month guard and
permissions, validates all saved cash sources and the daily calculation, then inserts every PDF
and the receipt atomically. A post-write reader verifies every original and rebuilds the result
before the transaction can commit. Failures roll back all versions and revision increments.

The server receipt binds account/draft/month, owner/server timestamp, source revision, original
archive and individual PDF fingerprints, the cash-source receipt and four existing source hashes,
both broker binding hashes, exact predecessor fingerprints, daily coverage and the calculation
digest. It explicitly does not claim independent cross-account broker linkage, archive-byte
retention, financial approval or publication authorization. A revision-independent evidence digest
allows unrelated revision increments while still revalidating the complete source graph.

An exact repeated save is a no-op. Replacements require explicit confirmation and preserve every
superseded original and receipt. New later daily dates can be added. Removing previously saved
dates requires a separate source review; this path does not delete or silently orphan originals.
Existing orphan/missing PDFs, mismatching fingerprints or invalid receipts are rejected without
adoption or repair. An earlier saved daily review must still verify before replacement; changes
to its underlying cash/binding/predecessor context require separate review rather than automatic
receipt repair.

Saved viewing uses one repeatable read-only gateway, checks current account/draft permissions
and revision, verifies the exact current PDF-role inventory and every original, then rebuilds the
review using the retained PDF bytes. It does not require a reupload and performs no writes.
The UI clears candidate review/checkbox state when the account, draft revision or archive bytes
change. Source confirmation controls are hidden for read-only, archived and published views;
server-side ownership and publication checks remain decisive.

The fixed disposable PostgreSQL extension checks failures after first/last PDF and receipt writes,
caught forbidden SQL, concurrent one-winner saves, invisible partial versions in an independent
repeatable reader, no-op repetition, owner attribution, read/view/edit/revoked permissions, stale
requests, published-month preservation, replacement rollback, superseded PDF/receipt retention,
read-only saved-view SQL, corruption rejection and unchanged financial artifacts. It uses only
synthetic PDFs in a new clone of the pinned local restore. The retained restore is read only and
exact identity cleanup is retained. Production is never targeted.

Next work remains explicit monetary/period source policy and financial candidate/writer integration.
Daily confirmation endpoints and later unchanged export evidence remain distinct. Neither saving
originals nor passing this rehearsal authorizes October financial publication.
