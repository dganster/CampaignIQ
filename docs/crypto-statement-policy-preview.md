# Proposed statement-backed crypto accounting

This is an unapproved, read-only source-policy preview, not a replacement for any
published crypto month. Its format is campaigniq.crypto_statement_policy_preview;
the production crypto importer and publication guards are unchanged.

## Proposed source choices

- Carry cash and units from the preceding dated statement's closing balances.
- Use current statement trade amounts as the proposed economic cash amounts.
  Fees printed beside each trade are already included in those amounts. A buy's
  proposed lot cost is its full cash debit, including fees. A sale's proposed net
  proceeds are its cash credit; the fee is not deducted a second time.
- Keep exact matched export quantities, execution times and full-precision prices.
  Preserve printed prices too. Do not reprice statement cash using either price.
- Keep every original export amount, running cash discrepancy, printed opening
  balance conflict and individual statement/export difference in the review.
- Use current dated statement holdings for the ending valuation. Do not use an
  undated export snapshot as the valuation date or as proof of cost basis.

The complete preceding/current statement and export comparison must pass its
independent evidence checks before a proposal is built. A source choice cannot
bypass a quantity, fee, funding, coverage, ending balance or ambiguous-match failure.
No cent differences are silently waived or rounded away. The proposal shows each
exact source amount and its difference, even when the monthly sums balance.

## Cost and performance limits

Carried units are linked to the preceding statement's acquisition rows when all
trading rows for that instrument are buys, their exact quantities sum to its closing
holding and their cash amounts and fees are valid. Each acquisition remains a
separate lot with its printed cost including fees, original date and source row.
Sales, unit transfers or incomplete matching quantities leave opening basis unknown.
The prior statement's market value never supplies cost. A separately verified
retained lot state could establish other opening basis in a later integration.
No preceding published state is changed by this calculation.
Current-month buys create proposed lots from current statement cash. FIFO sales
consuming unknown opening basis retain unknown realized profit. Same-month buys
and sales can have known proposed costs. Each source execution remains separate.

The preview also shows the change in holdings-plus-cash after subtracting external
funding. This is a proposed dollar value change, not a time-weighted return,
taxable gain or verified whole-account performance. Trade fees are already reflected
in cash; transfers are never treated as trading profit. It does not invent an
intraday order for statement cash transactions listed without execution times.

## Review and activation boundary

The three exact original hashes and a digest of the complete discrepancy review,
including the opening acquisition proof, bind the proposal. Any source change
produces a different review and preview hash.
policy_approved, account_association_confirmed, original_discrepancies_resolved,
financial_state_written and publication_authorized are always false. The full review
continues to report unresolved original controls. There is no approval switch here.

An eventual saved owner association, explicit review of these source choices,
account-scoped receipt integration, current-source revalidation, full financial
generation and restricted database verification are still necessary. Matching masked
suffixes alone does not establish account ownership. No published September history
is replaced, no original is changed and no production or database connection is made.

The user's accounting-source approval was given in the development conversation.
That approval must still be bound to the current signed-in account and exact saved
source documents in the eventual account workflow. The pure preview does not turn
a conversation-level approval into a database receipt or publication permission.

## Local command

    python3 -m campaigniq.crypto_statement_policy_preview \
      --development-only --year 2026 --month 10 \
      --trade-history /path/to/original-history.csv \
      --previous-statement /path/to/previous-crypto-statement.pdf \
      --current-statement /path/to/current-crypto-statement.pdf

Exit 0 means an unapproved preview was produced; it is not publication authority.
Exit 1 means invalid or incomplete evidence blocked the preview. Output contains
financial details and should be kept private. PDFs are extracted only into temporary
private directories, and source hashes are verified again after calculation.
