# Account closing-inventory preview (local synthetic development)

The interactive preview adds Preview closing inventory. It reauthorizes the
selected account and locks the selected saved draft, restages its original
sources in a private temporary directory, and checks every document's broker
binding before financial preflight. Missing identity or required inputs block
execution. Stale/foreign drafts cannot be used; view grants permit only reading.

After these checks it invokes the existing execute_monthly_import function with
preview_only=True, no history source root and no reconciliation decision. Its
artifact adapter explicitly rejects every write or delete, in addition to the
underlying gateway's read-only authorization. No lot book, storage connection or
temporary path escapes; the result contains immutable quantity summaries and
mismatches. Temporary files are removed even if preview raises an error.

The result covers closing inventory, not full financial approval, independent
crypto holdings, tax lots or full portfolio continuity. Forex source findings
also block the displayed reconciled status. The existing financial pipeline,
market applicability rules, broker identity checks, reconciliation policy and
publication modules are unchanged. There is no finalize/publish button or API.

The included synthetic January 2025 fixture starts with 100 SPY shares and buys
10 at 100. The independent closing statement reports 110; the mismatch fixture
reports 111 and must produce a difference of -1, without auto-repair. It contains
no real people, real account identifiers, Forex, crypto, options or realized
sales. Forex and crypto are explicitly marked not applicable and still checked
by the existing applicability validator. This fixture verifies one simple equity
scenario; it does not establish support for every real broker document format.

The local SQL runner creates a separate synthetic registry and drafts, checks
matching/mismatching closing statements, stale/foreign access, view/revocation,
write rejection and table preservation, then retains a correct demo month.
Original source versions, including the deliberately mismatching statement,
remain in the test draft's version history. A private preview configuration is
printed for --resume so the complete month can be opened without four manual
uploads. Existing previews and financial data are preserved. No production
connection, data migration or publication occurs.

Run from the development checkout:

    PYTHONPATH=src python -m campaigniq.account_reconciliation_rehearsal --development-only

Only after this succeeds, open the printed configuration using the usual
account_import_preview_ui launcher with --development-only --resume <path>.
For this fixture uncheck Forex applicable and Crypto applicable, then click
Preview closing inventory. The displayed closing quantity should be 110.
