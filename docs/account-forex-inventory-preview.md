# October Forex opening and closing inventory review

This checkpoint adds an account-scoped, one-shot read-only review. After saving the existing
Forex cash-source receipt, supply the original **October 2026 Forex monthly PDF**, headed
`Customer Account Summary Report`. The futures/options GMI statement is a separate report.
The PDF is temporary: this checkpoint does not save, approve, or publish it.

The service uses the existing repeatable read-only gateway. It rechecks current account access,
draft revision, the saved owner receipt, all four source fingerprints and both broker bindings.
Every monthly/daily Forex PDF section must identify the current Forex account. The supported
statement layout requires the monthly summary, first daily boundary, explicit position details
whose units sum to totals, complete table headings/footers, and matching execution references,
dates, pairs, units, rates and economic OPEN/CLOSE effects. Daily coverage must not overlap
or extend beyond the monthly statement's last day. The first daily boundary cannot follow an
execution. Unsupported layouts or ambiguous rows block the review.

A labelled temporary execution projection selects only existing October TRD rows from the
saved cash original. Original source bytes remain unchanged. The result retains the projection
fingerprint and original section row/reference mapping. Full-year cash is never replayed as
October activity, and the missing CSV execution is not invented or written into the original.
The existing saved cash review independently requires exact parity with base/supplemental
execution exports. Its original monetary discrepancy and missing-reference findings remain.

The exact published September 30 lot state is loaded using the existing predecessor discovery
rules; any applicable publication protocol and marker are also validated and fingerprinted.
Legacy predecessor semantics remain supported. Wrong-period states, duplicate identities,
nonfinite quantities and Forex lots opened after the boundary are rejected. Statement quantities
must match the existing Forex opening lots. A clone is replayed through the existing Forex
position resolver, translator and FIFO lot book, then compared to the independent PDF closing
quantities. Prior lot/campaign IDs are retained in close allocations. The opening book is checked
unchanged. No authoritative state, basis or campaign identity is created or replaced.

Without a published predecessor, the result explicitly reports incomplete authoritative opening
verification and returns no seeded/replayed lots. Statement inventory corroboration alone does
not establish complete account continuity. A successful position review also does not resolve
monetary source differences, independently link securities/Forex accounts, or authorize financial
publication. The current Forex publication blocker stays in place.

The Data view makes a fresh service call for each review and stores no approval or cached
result. Changing account, revision or PDF requires a new review. View permissions are rechecked
by the server. Production startup/configuration/source release approval remains separate.

## Local verification

The development installer first runs the full suite, then the existing fixed
`--development-only --forex-cash-source-receipts` PostgreSQL rehearsal. The extension uses an
actual synthetic PDF and new synthetic predecessor fixtures in the disposable clone, checks
exact FIFO lot/campaign retention and unchanged source rows, view/revoked access, stale/foreign
requests, SQL read-only enforcement and conflicting predecessor rejection without repair.
The common harness preserves the pinned restore and removes only its exact generated clone
and roles after checks. No production target or user source documents are packaged.

Actual October evidence acceptance remains required: the most recent saved Forex monthly PDF
reviewed during development covers September 2026, while the October GMI PDF covers futures.
Do not treat the synthetic PDF or a passing local rehearsal as verification of October's actual
Forex positions. Durable monthly-PDF receipts and financial candidate/writer integration remain
subsequent work.
