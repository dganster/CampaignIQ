# Statement-only months without trading

An account owner can review a month with no trading using its brokerage statement alone. Empty trade-history and realized gain/loss reports are not manufactured. The existing statement uploader is unchanged; the new `No trading activity this month` section reviews carried lots and separate cash income, then saves one explicit owner confirmation. Publication still requires the separate account/month authorization.

## What qualifies

An exact preceding published month must exist. The statement's identity and full calendar period must match the account and draft. The statement must explicitly report zero purchases, sales, transfers, expenses, other activity and realized gains/losses. Every transaction detail must be a supported positive cash dividend or credited interest payment, fully reconciled to both transaction total and the statement cash equation. Zero-income statements with an explicit complete zero transaction summary/detail total are supported. Unrecognized layouts or activity are withheld for normal review.

Closing position quantities and known reported cost bases must match the unchanged canonical opening book. An option expiring on or before the selected month end, pending statement activity, opening Forex positions, carried crypto, prior futures evidence or current saved market-source approvals requires the normal workflow. This first version covers statement-only securities months; it does not bypass Forex, crypto or futures controls.

Full first-page Schwab account headers can be followed by matching `****-*123` continuation-page headers. A masked header cannot establish identity, precede the full header, disagree with it or introduce a second account. No account identity is inferred from filenames or trade amounts.

## Preserved state and separate income

The complete canonical book is cloned without re-seeding. Quantities, opening dates, costs, basis sources, lot IDs, campaign IDs and the next-lot counter remain identical. The month receives its ordinary lot state, carried-position journal, empty realized/Forex attributions, empty lifecycle transitions, completeness, market choices and original statement provenance. A source-bound `YYYY-MM-no-trading-month.json` records the itemized statement cash income separately from zero realized trading P/L. It is referenced by the normal account publication receipt.

The published result and dashboard show statement dividends/interest separately; existing trading totals and campaign win/loss metrics keep their definition. The statement's cash opening and closing are reported cash controls, not a newly established authoritative cash ledger. Without a saved preceding statement cash control, only the current statement cash equation is checked. Canonical position continuity always uses the actual preceding published book.

## Atomic writes and independent reads

The complete generation is built and checked before insertion. The existing serialized restricted publisher appends it in one transaction with the visibility marker last. A failure after any component, receipt or marker rolls back the whole month. Existing published periods cannot be replaced. Independent authorized results rebuild the expected book, journal, income and canonical artifacts from original sources and the owner confirmation in one read-only snapshot. Self-consistent forged journal/hash/receipt combinations are withheld without repair.

The owner confirmation binds account, draft, exact statement bytes, preceding published lot artifact and binding. Stale revisions, changed sources, foreign owners, missing choices or disabled publication capability cannot publish. No separate account schema, elevated runtime permissions, synthetic broker uploads or production data migration is introduced.

## Verification and release boundary

Unit checks cover no-trading publication, dividends separate from zero trading P/L, exact lot/campaign preservation, following-month opening, expiry-month rejection, missing/wrong sources, cash/detail discrepancies, owner/revision/capability controls, every component rollback and coherent tampering. The supplied real September/October PDFs were checked offline using synthetic canonical IDs; this does not verify or modify Andrew's saved production state.

The existing disposable `forex_cash_sources` PostgreSQL rehearsal now also creates a separate synthetic no-trading account. It tests real confirmation rollback, restricted generation rollback, caught forbidden SQL, duplicate publication concurrency, repeatable readers, independent income/lot verification, read-only SQL, edit/view/revocation and coherent corruption. Existing retained-restore and copied-row comparisons remain in place. Only the local user machine can run that rehearsal against its retained development restore.

Install from clean development commit `e66f856`. The installer runs the full local suite and the disposable PostgreSQL rehearsal. It does not deploy, change production settings, publish Andrew's October, or create an Andrew session. Production validation against his actual saved September book and authorized publication remain subsequent steps.
