# Account-scoped Forex cash review

Adds an October 2026 read-only review to Data. Candidate CSV bytes stay in temporary
files and are deleted after evaluation. No original replacement, receipt, financial writer,
configuration change or publication integration is introduced.

Uses the existing deployment gateway repeatable read-only transaction and draft/source
checksum verification. Every request rechecks current account access, displayed revision,
saved Trade History and Forex report, and separate securities/Forex directory bindings.
Uploaded cash and supplemental execution exports must match the securities header binding.
This is not independent proof linking securities and Forex broker accounts. The calculation
continues to mark identity and closing inventory unverified; the wrapper describes only
header-to-directory checks. Original discrepancies and exact source hashes are displayed.

The UI performs a one-shot review and caches no approval. All existing reconciliation and
publication blockers remain active. Next work: durable owner source receipts, authoritative
opening/closing validation, financial candidate/writer integration and database rehearsal.
