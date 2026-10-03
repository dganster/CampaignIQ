"""Direct navigation and evidence-backed labels for campaign inspection."""

from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from campaigniq.analytics.campaign_drilldown_summary import CampaignDrilldownSummary
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.importers.thinkorswim.trade_history_reader import ThinkorswimTradeHistoryReader
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader
from campaigniq.ui.analytics_navigation import publish_route, remember_return


def open_chart_month(state, event, months):
    points = event.get("selection", {}).get("campaign_month", [])
    if not points:
        return
    open_reporting_month(state, points[0].get("month_key"), months)


def open_reporting_month(state, month, months):
    if month not in months:
        return
    state["campaigniq_campaign_month"] = month
    state["campaigniq_campaign_symbol"] = "All symbols"
    state["campaigniq_campaign_result"] = "All results"
    state["campaigniq_campaign_detail"] = None
    state["campaigniq_primary_view"] = "Campaigns"
    # A fresh chart selection on returning to Overview can reopen the same bar.
    state["campaigniq_overview_chart_generation"] = state.get("campaigniq_overview_chart_generation", 0) + 1


def apply_performance_typography(ui):
    """Balance metric labels and values while the Performance view is open."""
    ui.html("""<style>
    [data-testid="stMetricLabel"] p {
        font-size: 1.05rem;
        font-weight: 600;
        line-height: 1.4;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.65rem;
        line-height: 1.3;
    }
    [data-testid="stMetricDelta"] {
        font-size: 0.9rem;
    }
    </style>""")


def render_performance_months(ui, table, months, column_config):
    fingerprint = hashlib.sha256(json.dumps(list(months)).encode()).hexdigest()[:20]
    key = f"campaigniq_performance_months_{fingerprint}"

    def selected():
        rows = ui.session_state.get(key, {}).get("selection", {}).get("rows", [])
        index = rows[0] if rows else None
        if isinstance(index, int) and 0 <= index < len(months):
            if hasattr(ui, "query_params"):
                remember_return(ui)
            open_reporting_month(ui.session_state, months[index], months)
            if hasattr(ui, "query_params"):
                publish_route(ui)

    ui.dataframe(table, hide_index=True, width="stretch",
                 column_config=column_config, key=key,
                 on_select=selected, selection_mode="single-row")


def render_month_chart(ui, chart, months):
    fingerprint = hashlib.sha256(json.dumps(list(months)).encode()).hexdigest()[:12]
    key = f"campaigniq_monthly_chart_{ui.session_state.get('campaigniq_primary_view', 'Overview')}_{fingerprint}_{ui.session_state.get('campaigniq_overview_chart_generation', 0)}"

    def selected():
        event = ui.session_state.get(key, {})
        points = event.get("selection", {}).get("campaign_month", [])
        if not points or points[0].get("month_key") not in months:
            return
        if hasattr(ui, "query_params"):
            remember_return(ui)
        open_chart_month(ui.session_state, event, months)
        if hasattr(ui, "query_params"):
            publish_route(ui)

    ui.altair_chart(chart, use_container_width=True, key=key,
                    on_select=selected, selection_mode="campaign_month")


def build_month_chart(frame):
    """One chart definition shared by Overview and Performance."""
    import altair as alt

    data = frame[["period_start", "realized_pnl"]].sort_values("period_start").copy()
    data["month_label"] = data["period_start"].map(lambda value: value.strftime("%b %Y"))
    data["month_key"] = data["period_start"].map(lambda value: value.strftime("%Y-%m"))
    data["result"] = data["realized_pnl"].map(lambda value: "Gain" if value >= 0 else "Loss")
    selection = alt.selection_point(name="campaign_month", fields=["month_key"], toggle=False)
    chart = (alt.Chart(data).mark_bar(cursor="pointer").encode(
        x=alt.X("month_label:N", sort=data["month_label"].tolist(),
                title="Reporting month", axis=alt.Axis(labelAngle=0)),
        y=alt.Y("realized_pnl:Q", title="Realized P&L (USD)"),
        color=alt.Color("result:N", scale=alt.Scale(domain=["Gain", "Loss"],
                        range=["#287d59", "#c44949"]), legend=None),
        tooltip=[alt.Tooltip("month_label:N", title="Month"),
                 alt.Tooltip("realized_pnl:Q", title="Realized P&L", format="$,.2f")],
    ).properties(height=320).add_params(selection))
    return chart, tuple(data["month_key"])


def choose_campaign_row(state, event, campaign_ids):
    rows = event.get("selection", {}).get("rows", [])
    index = rows[0] if rows else None
    state["campaigniq_campaign_detail"] = (
        campaign_ids[index]
        if isinstance(index, int) and 0 <= index < len(campaign_ids)
        else None
    )


def render_campaign_table(ui, table, campaign_ids, column_config):
    # Changing filters gives the table a fresh selection state, preventing an
    # old row offset from selecting a different campaign in the new table.
    fingerprint = hashlib.sha256(json.dumps(list(campaign_ids)).encode()).hexdigest()[:20]
    key = f"campaigniq_campaign_rows_{fingerprint}_{ui.session_state.get('campaigniq_selection_generation', 0)}"

    def selected():
        event = ui.session_state.get(key, {})
        rows = event.get("selection", {}).get("rows", [])
        if rows and isinstance(rows[0], int) and 0 <= rows[0] < len(campaign_ids):
            if hasattr(ui, "query_params") and campaign_ids[rows[0]] != ui.session_state.get("campaigniq_campaign_detail"):
                remember_return(ui)
        choose_campaign_row(ui.session_state, event, campaign_ids)
        if hasattr(ui, "query_params"):
            publish_route(ui)

    ui.dataframe(table.style.format({"Realized P&L": "{:,.2f}"}),
                 use_container_width=True, hide_index=True,
                 column_config=column_config, key=key,
                 on_select=selected, selection_mode="single-row")


def campaign_month(campaign):
    """Use the reporting-period prefix, never a campaign's opening month."""
    return campaign.campaign_id.split("/", 1)[0]


def forex_drilldowns(monthly):
    """Expose the same settled FOREX evidence included in monthly bar totals."""
    records = {}
    for (start, _end), attributions in sorted(monthly.items()):
        for attribution in attributions:
            pair = attribution.settlement.instrument
            cid = f"{start:%Y-%m}/FOREX:{quote(pair, safe='')}:{attribution.campaign_id}"
            records.setdefault(cid, []).append(attribution)
    summaries = []
    for cid, attributions in records.items():
        dates = [a.settlement.settlement_at.date() for a in attributions]
        summaries.append(CampaignDrilldownSummary(
            campaign_id=cid,
            symbols=tuple(sorted({a.settlement.instrument for a in attributions})),
            realized_pnl=sum((a.gain_loss for a in attributions), Decimal("0")),
            record_count=len(attributions), allocation_count=0,
            first_closed_date=min(dates), last_closed_date=max(dates),
            fully_reconciled=True,
        ))
    return tuple(summaries), {cid: tuple(attrs) for cid, attrs in records.items()}


def read_closing_trades(root, closed_dates):
    """Read only this workspace's archived reports around the close month.

    An absent or unreadable report leaves direction unavailable; it never
    changes realized economics or invents a position side.
    """
    months = set()
    for day in closed_dates:
        prior_day = day - timedelta(days=1)
        while prior_day.weekday() >= 5:
            prior_day -= timedelta(days=1)
        months.update((day.replace(day=1), prior_day.replace(day=1)))
    trades, incomplete = [], False
    for month in sorted(months):
        path = Path(root) / f"Account Trade History {month:%B %Y}.csv"
        if not path.is_file():
            incomplete = True
            continue
        try:
            statement = ThinkorswimSourceReader().read(str(path))
            orders = ThinkorswimTradeHistoryReader().read(statement.section("Account Trade History"))
            trades.extend(to_trade(order) for order in orders
                          if all(row.option_type.upper() not in {"FOREX", "CRYPTO"} for row in order.legs))
        except Exception:
            incomplete = True
    # Partial evidence must not silently establish a side.
    return (() if incomplete else tuple(trades)), incomplete


def _next_weekday(day):
    result = day + timedelta(days=1)
    while result.weekday() >= 5:
        result += timedelta(days=1)
    return result


def closed_position_side(record, trades):
    """Identify long/short only from unambiguous matching TO CLOSE evidence.

    Option broker dates can be execution day or the next weekday, matching
    the ordinary option attribution convention. Conflicting dates/sides or
    insufficient closing quantity remain unavailable. Tax term is not side.
    """
    candidates = []
    for trade in trades:
        for leg in trade.legs:
            if leg.instrument != record.instrument or leg.position_effect is not PositionEffect.CLOSE:
                continue
            for execution in leg.executions:
                day = execution.executed_at.date()
                if day == record.closed_date or (
                    isinstance(record.instrument, OptionContract)
                    and _next_weekday(day) == record.closed_date
                ):
                    candidates.append((day, leg.side, abs(execution.quantity)))
    if (not candidates or len({x[0] for x in candidates}) != 1
            or len({x[1] for x in candidates}) != 1
            or sum((x[2] for x in candidates), Decimal("0")) < record.quantity):
        return "Unavailable"
    side = candidates[0][1]
    return "Short" if side is Side.BUY else "Long" if side is Side.SELL else "Unavailable"


def closed_quantity_units(instrument):
    return "Contracts" if isinstance(instrument, OptionContract) else "Shares"
