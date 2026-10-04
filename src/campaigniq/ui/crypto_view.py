"""Crypto evidence review, with exact fractional quantities and explicit limits."""

from decimal import Decimal
from campaigniq.ui.table_layout import render_dataframe


def render_crypto_report(report: dict, st) -> None:
    st.subheader("Crypto")
    st.caption(f"{report['period_start']} through {report['period_end']} · Separate crypto account")
    st.info("Crypto records are provisional export evidence. Crypto results are shown separately from the brokerage and FOREX totals.")
    fills = report["fills"]
    known_fees = sum((Decimal(row["fee_usd"]) for row in fills if row["fee_usd"] is not None), Decimal(0))
    missing_fees = sum(row["fee_usd"] is None for row in fills)
    a, b, c = st.columns(3)
    a.metric("Crypto fills", len(fills))
    b.metric("Recorded fees", f"${known_fees:,.2f}" if not missing_fees else "Incomplete")
    cash = report.get("last_reported_cash_usd")
    c.metric("Last reported cash", f"${Decimal(cash):,.2f}" if cash is not None else "Unavailable")
    if report["cash_control_reconciled"]:
        st.success("Cash running balances reconcile within the supplied export, including fees and funding.")
    else:
        st.warning("Cash running balances need review.")
    if report["fill_control_reconciled"]:
        st.success("Execution fills match the crypto cash-ledger fills, including partial fills sharing a reference.")
    else:
        st.warning("Execution history and crypto cash-ledger fills need review.")
    for warning in dict.fromkeys(report["warnings"]):
        st.warning(warning)
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
                              "Remaining FIFO cost incl. fees": f"${cost:,.2f}" if cost is not None else "Unknown opening cost"})
        if positions:
            render_dataframe(st, positions, use_container_width=True, hide_index=True)
        st.caption("FIFO costs and realized results are calculated from available crypto cash evidence; they are not a broker tax report.")
        if report["realized_sales"]:
            st.markdown("#### Realized crypto sales (FIFO)")
            render_dataframe(st, report["realized_sales"], use_container_width=True, hide_index=True)
        if report["snapshot_mismatches"]:
            st.warning("Computed crypto quantities differ from the export snapshot or snapshot evidence is missing.")
            render_dataframe(st, report["snapshot_mismatches"], use_container_width=True, hide_index=True)
    st.markdown("#### Individual fills")
    if fills:
        render_dataframe(st, [{"Source execution time": row["executed_at"], "Pair": row["pair"],
                       "Side": row["side"], "Quantity": format(Decimal(row["quantity"]), "f"), "Price (USD)": row["price_usd"],
                       "Principal (USD)": row["principal_usd"], "Fee (USD)": row["fee_usd"],
                       "Reference": row["reference"]} for row in fills],
                     use_container_width=True, hide_index=True)
    else:
        st.info("No crypto execution fills in the selected month.")
    with st.expander("Crypto cash ledger"):
        st.caption(f"Net funding: ${Decimal(report['net_funding_usd']):,.2f}. Funding transfers are not trading profit.")
        render_dataframe(st, report["cash_ledger"], use_container_width=True, hide_index=True)
