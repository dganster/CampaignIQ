"""Crypto evidence review, with exact fractional quantities and explicit limits."""

from decimal import Decimal
from campaigniq.ui.table_layout import render_dataframe
from campaigniq.ui.crypto_evidence_view import has_statement_choices, render_crypto_limitations
from campaigniq.ui.financial_format import display_money, display_exact_money, display_quantity


def render_crypto_report(report: dict, st) -> None:
    st.subheader("Crypto")
    st.caption(f"{report['period_start']} through {report['period_end']} · Separate crypto account")
    approved = has_statement_choices(report)
    render_crypto_limitations(st, status=report.get('status', ''), warnings=report['warnings'], report=report)
    fills = report["fills"]
    known_fees = sum((Decimal(row["fee_usd"]) for row in fills if row["fee_usd"] is not None), Decimal(0))
    missing_fees = sum(row["fee_usd"] is None for row in fills)
    a, b, c = st.columns(3)
    a.metric("Crypto fills", len(fills))
    b.metric("Export-recorded fees", display_money(known_fees) if not missing_fees else "Incomplete")
    cash = report.get("last_reported_cash_usd")
    c.metric("Statement closing cash" if approved else "Export-reported cash", display_money(Decimal(cash)) if cash is not None else "Unavailable")
    if not approved and not report['cash_control_reconciled']:
        c.caption('Unverified closing balance; running-balance control needs review.')
    if "ending_quantities" in report:
        st.markdown("#### Crypto holdings")
        st.caption(f"Opening quantities: {report['opening_source']}. Exact quantities are retained; snapshot quantities may be rounded.")
        positions = []
        for pair in sorted(set(report["ending_quantities"]) | set(report["holdings_snapshot"])):
            snapshot = report["holdings_snapshot"].get(pair, {})
            lots = report["ending_lots"].get(pair, [])
            known = all(lot["cost_usd"] is not None for lot in lots)
            cost = sum((Decimal(lot["cost_usd"]) for lot in lots), Decimal(0)) if known else None
            positions.append({"Pair": pair, "Computed quantity": report["ending_quantities"].get(pair, "0"),
                              "Export snapshot quantity": snapshot.get("quantity", "Unavailable"),
                              "Remaining acquisition cost incl. fees": display_money(cost) if cost is not None else "Unknown opening cost"})
        if positions:
            render_dataframe(st, positions, use_container_width=True, hide_index=True)
        st.caption("Acquisition cost is not current market value. FIFO costs and realized results use available crypto cash evidence; they are not a broker tax report.")
        if report["realized_sales"]:
            st.markdown("#### Realized crypto sales (FIFO)")
            render_dataframe(st, report["realized_sales"], use_container_width=True, hide_index=True)
        if report["snapshot_mismatches"]:
            st.caption("Original export quantity comparison: these retained differences describe the original export calculation, not a new comparison against the published statement-based holdings.")
            render_dataframe(st, [{{'pair': 'Pair', 'computed': 'Original export computed quantity', 'snapshot': 'Export snapshot quantity'}.get(key, key): value for key, value in row.items()} for row in report['snapshot_mismatches']], use_container_width=True, hide_index=True)
    st.markdown("#### Individual fills")
    st.caption("Original export execution observations. Principal and fees here are export values; they do not replace approved statement cash amounts.")
    if fills:
        render_dataframe(st, [{"Source execution time": row["executed_at"], "Pair": row["pair"],
                       "Side": row["side"], "Quantity": display_quantity(row["quantity"]), "Price (USD)": display_exact_money(row["price_usd"]),
                       "Principal (USD)": display_money(Decimal(row["principal_usd"])) if row["principal_usd"] is not None else 'Unavailable',
                       "Fee (USD)": display_money(Decimal(row["fee_usd"])) if row["fee_usd"] is not None else 'Unavailable',
                       "Reference": row["reference"]} for row in fills],
                     use_container_width=True, hide_index=True)
    else:
        st.caption("No crypto execution fills in the selected month.")
    with st.expander("Crypto cash ledger"):
        st.caption("Original export cash observations; unresolved running-balance differences remain retained.")
        st.caption(f"Net funding: ${Decimal(report['net_funding_usd']):,.2f}. Funding transfers are not trading profit.")
        labels = {'amount_usd': 'Cash change (USD)', 'balance_usd': 'Reported balance (USD)',
                  'description': 'Description', 'executed_at': 'Source date & time',
                  'fee_cash_change_usd': 'Fee cash change (USD)'}
        cash_columns = {'amount_usd', 'balance_usd', 'fee_cash_change_usd'}
        ledger = [{labels.get(key, key):
                   (display_money(Decimal(value)) if value is not None else 'Unavailable')
                   if key in cash_columns else value
                   for key, value in row.items()} for row in report['cash_ledger']]
        st.caption('Timestamps retain the source representation; no timezone conversion is applied here.')
        if ledger:
            render_dataframe(st, ledger, use_container_width=True, hide_index=True)
        else:
            st.caption("No ledger rows available.")
