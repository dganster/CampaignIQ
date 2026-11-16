# Bounded retained-income PDF warm-up

Cold retained-income verification can warm the existing pure PDF-text cache with
two workers. Only checksum-valid original PDF bytes for the authorized published
month set are candidates. Batches above 32 distinct PDFs or 8 MiB of input bytes,
single misses and fully warm batches retain the ordinary sequential path.

Workers receive PDF bytes only. They do not receive database connections,
directories, account identities, bindings, receipts or parsed financial results.
The existing 128-entry, 32-MiB extraction cache retains its exact-byte and tool
identity checks and failed extractions are never cached. The main thread still
performs every original checksum, published provenance, account binding, period
and cash reconciliation check. Warm-up produces no evidence or income result.

The disposable PostgreSQL rehearsal compares warmed and sequential retained-income
evidence through real restricted read-only snapshots and checks unchanged rows.
The release review must independently verify retained production histories and
income coverage. Actual cold-load benefit must be measured; no latency target is
assumed. New fixed timing stages record income_pdf_prewarm and
income_statement_verification. Nested durations must not be added together.
