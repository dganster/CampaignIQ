# Persistent published-provenance statement text

Retained-income reads can reuse PDF layout text from a private disk cache. Every
hit requires the current original bytes, their checksum, the published provenance
text SHA-256 and exact byte size. Account identity, statement period, cash controls,
ambiguity checks and income parsing still run. Original and financial rows are
never cached as authorization or report results. Raw-PDF-only provenance cannot
validate cached layout text and retains the original extraction path.

Only the normal signed-in deployment render enables this cache. Its path is
derived from the already verified private release layout:
`account-runtime/statement-text-v1`, beside the versioned runtime directories.
The start command and configuration keys remain unchanged. Composition, startup
preflight and command-line release review do not enable or populate it. A release
profile must explicitly use a temporary directory for cache measurements.

The directory is owned by the service user with mode 0700; text and lock files
are 0600. Canonical paths, no-follow opens, regular-file and ownership checks,
single-link entries, nonblocking writer locks and atomic replacement protect
cache reads and writes. Read-only storage, corruption, unsafe paths, contention
and disk failures fall back to fresh extraction. No original or financial source
is repaired. File names contain digests rather than account or document names.

Committed cache text is bounded to 128 entries and 32 MiB, with 4 MiB per entry.
One private pending file can temporarily use up to another 4 MiB; writers recover
their own interrupted pending file. Oldest entries are evicted under the writer
lock. Cache hits do not rewrite files. An absent cache simply starts empty after
fresh original validation. Deleting this derived directory loses only performance.

The unhelpful two-worker income warm-up is no longer invoked. Its standalone
extraction helper remains for compatibility. New sanitized fixed timing stages
show cache read/write attempts, hits, misses, rejections and successful stores, without source identifiers.
These nested stage durations cannot be added to page durations.

The disposable PostgreSQL rehearsal compares uncached, populated and disk-backed
income evidence after clearing in-memory PDF text between reads, on authorized
restricted read-only sessions. The production release review must still verify
both accounts' retained histories and financial results. A fresh-process cache
test and wrong-account, changed-provenance and corruption tests cover reuse.
