# Read-only consecutive-statement crypto review

This tool compares a full-month original Trade History with the preceding and
current original Schwab Crypto statement PDFs. It never writes financial state,
changes import tolerances, repairs originals or authorizes publication.

Run locally, with --development-only:

    python3 -m campaigniq.crypto_source_review \
      --development-only --year 2026 --month 10 \
      --trade-history /path/to/original-history.csv \
      --previous-statement /path/to/previous-crypto-statement.pdf \
      --current-statement /path/to/current-crypto-statement.pdf

The JSON is a private review, not a reconciliation approval. Exit code 0 indicates
all compared controls reconcile exactly; exit code 2 indicates a completed review
with differences requiring review; exit code 1 indicates missing/invalid evidence.
Every result has publication_authorized=false, including a fully matching review.
No database connection is created. Poppler extracts each PDF into a temporary private
directory. Exact input hashes are checked again after comparison.

## Evidence matching

Require consecutive full calendar months and matching crypto suffixes across both
statements and export. A suffix match is explicitly not proof of full account
ownership or an account-directory binding. The securities account is never reused
as a crypto identifier. An eventual account association must be a separate workflow.

Match every partial fill by execution date, side, instrument, exact quantity and
exact fee. A printed price is either exact, consistent with truncation of the fuller
export price to its printed precision, or different. This classification is diagnostic
only; it does not change the existing strict statement attachment importer. Ambiguous
distinct full-precision candidates are withheld, not paired by source order. Repeated
identical legs remain separate; broker order references do not deduplicate them.

Compare each statement trade amount with the export principal plus fee and retain
both, their exact signed difference and both source row identifiers. Compare funding
by execution date and amount with multiplicity preserved. Unknown ledger activity,
coverage gaps, unmatched fills, ending-quantity conflicts and ending-cash conflicts
each fail a separate evidence check. No one-cent tolerance exists.

## Carry-forward without changing source values

The review reports the current statement's printed opening cash, the preceding
statement's closing cash, the export opening cash, each statement's own cash roll-
forward, the export's overall roll-forward and the current month's roll-forward
using the preceding closing balance. None overwrites another. Internal export
running-balance differences remain visible even when their sum cancels to zero.
The previous statement's own inconsistencies remain flagged.

Ending units are checked from the preceding statement's holdings plus every current
statement trade. Current statement market values are checked against displayed
quantity and price at cent precision. Opening cost basis is never inferred from
market value, purchase totals or a balancing amount.

all_matched_evidence_checks_pass covers the separate comparisons and may be true
while all_controls_reconciled remains false. Any printed-opening inconsistency,
cash amount difference, nonexact export running balance or price-display difference
requires review. A reviewed accounting-source policy, account association and
publication integration are still pending. Existing strict import behavior and
futures publication guards remain unchanged in this development checkpoint.
