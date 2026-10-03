from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.ui.campaign_interactions import (
    campaign_month, choose_campaign_row, closed_position_side,
    closed_quantity_units, forex_drilldowns, open_chart_month,
    read_closing_trades, render_campaign_table, render_month_chart,
)


def test_chart_click_resets_filters_and_opens_selected_reporting_month():
    state = {"campaigniq_campaign_symbol": "CMI", "campaigniq_campaign_result": "Loss", "campaigniq_campaign_detail": "old"}
    open_chart_month(state, {"selection": {"campaign_month": [{"month_key": "2026-04"}]}}, ["2026-04"])
    assert state["campaigniq_primary_view"] == "Campaigns"
    assert state["campaigniq_campaign_month"] == "2026-04"
    assert state["campaigniq_campaign_symbol"] == "All symbols"
    assert state["campaigniq_campaign_result"] == "All results"
    assert state["campaigniq_campaign_detail"] is None
    assert state["campaigniq_overview_chart_generation"] == 1


@pytest.mark.parametrize("event", [{}, {"selection": {"campaign_month": []}}, {"selection": {"campaign_month": [{"month_key": "2020-01"}]}}])
def test_empty_or_stale_chart_event_does_not_navigate(event):
    state = {"campaigniq_primary_view": "Overview"}
    open_chart_month(state, event, ["2026-04"])
    assert state == {"campaigniq_primary_view": "Overview"}


def test_selected_row_uses_input_dataframe_position_after_browser_sort():
    state = {}
    choose_campaign_row(state, {"selection": {"rows": [1]}}, ["2026-04/A", "2026-04/B"])
    assert state["campaigniq_campaign_detail"] == "2026-04/B"


@pytest.mark.parametrize("rows", [[], [99], [-1]])
def test_empty_or_out_of_range_selection_clears_detail(rows):
    state = {"campaigniq_campaign_detail": "old"}
    choose_campaign_row(state, {"selection": {"rows": rows}}, ["new"])
    assert state["campaigniq_campaign_detail"] is None


class UI:
    def __init__(self):
        self.session_state = {}
        self.calls = []

    def dataframe(self, table, **kwargs):
        self.calls.append((table, kwargs))

    def altair_chart(self, chart, **kwargs):
        self.calls.append((chart, kwargs))


def test_table_callback_selects_campaign_and_filter_changes_reset_row_state():
    ui = UI()
    table = pd.DataFrame({"Realized P&L": [-6638.05, 1200.0]})
    render_campaign_table(ui, table, ["A", "B"], {})
    styled, kwargs = ui.calls[-1]
    assert "-6,638.05" in styled.to_html()
    assert kwargs["selection_mode"] == "single-row"
    ui.session_state[kwargs["key"]] = {"selection": {"rows": [1]}}
    kwargs["on_select"]()
    assert ui.session_state["campaigniq_campaign_detail"] == "B"
    render_campaign_table(ui, table.iloc[:1], ["B"], {})
    assert ui.calls[-1][1]["key"] != kwargs["key"]


def test_chart_callback_runs_navigation_before_widget_rerun_and_can_reopen_same_bar():
    ui = UI()
    render_month_chart(ui, object(), ["2026-04"])
    kwargs = ui.calls[-1][1]
    assert kwargs["selection_mode"] == "campaign_month"
    ui.session_state[kwargs["key"]] = {"selection": {"campaign_month": [{"month_key": "2026-04"}]}}
    kwargs["on_select"]()
    render_month_chart(ui, object(), ["2026-04"])
    assert ui.calls[-1][1]["key"] != kwargs["key"]


def option():
    return OptionContract("CMI", date(2026, 10, 16), Decimal("300"), OptionType.PUT)


def trade(instrument, side, day, quantity="1", effect=PositionEffect.CLOSE):
    return Trade((Leg(instrument, side, effect,
                      (Execution(Decimal(quantity), Decimal("5"), datetime.combine(day, datetime.min.time())),)),))


@pytest.mark.parametrize("side,expected", [(Side.BUY, "Short"), (Side.SELL, "Long")])
def test_closed_option_direction_is_position_side_not_tax_term(side, expected):
    record = SimpleNamespace(instrument=option(), closed_date=date(2026, 10, 2), quantity=Decimal("1"), term="SHORT")
    assert closed_position_side(record, [trade(option(), side, record.closed_date)]) == expected
    assert record.quantity == Decimal("1")
    assert closed_quantity_units(record.instrument) == "Contracts"


def test_option_settlement_date_across_weekend_and_month_boundary():
    record = SimpleNamespace(instrument=option(), closed_date=date(2026, 6, 1), quantity=Decimal("1"))
    assert closed_position_side(record, [trade(option(), Side.BUY, date(2026, 5, 29))]) == "Short"


def test_partial_fills_support_closed_quantity():
    record = SimpleNamespace(instrument=Instrument("CMI"), closed_date=date(2026, 10, 2), quantity=Decimal("10"))
    assert closed_position_side(record, [trade(record.instrument, Side.SELL, record.closed_date, "4"), trade(record.instrument, Side.SELL, record.closed_date, "6")]) == "Long"
    assert closed_quantity_units(record.instrument) == "Shares"


@pytest.mark.parametrize("reason", ["absent", "opening", "insufficient", "conflicting_sides", "adjacent_dates", "different_instrument"])
def test_direction_remains_unavailable_without_unambiguous_evidence(reason):
    day = date(2026, 10, 2)
    record = SimpleNamespace(instrument=option(), closed_date=day, quantity=Decimal("2"))
    trades = {
        "absent": [],
        "opening": [trade(option(), Side.BUY, day, "2", PositionEffect.OPEN)],
        "insufficient": [trade(option(), Side.BUY, day)],
        "conflicting_sides": [trade(option(), Side.BUY, day), trade(option(), Side.SELL, day)],
        "adjacent_dates": [trade(option(), Side.BUY, day), trade(option(), Side.BUY, date(2026, 10, 1))],
        "different_instrument": [trade(Instrument("CMI"), Side.BUY, day, "2")],
    }[reason]
    assert closed_position_side(record, trades) == "Unavailable"


def test_missing_archive_is_read_only_and_does_not_require_reimport(tmp_path):
    assert read_closing_trades(tmp_path, [date(2026, 10, 2)]) == ((), True)
    assert list(tmp_path.iterdir()) == []


def test_unreadable_archive_does_not_use_partial_evidence(tmp_path):
    (tmp_path / "Account Trade History October 2026.csv").write_text("broken")
    trades, incomplete = read_closing_trades(tmp_path, [date(2026, 10, 2)])
    assert trades == () and incomplete


def test_forex_drilldown_preserves_month_totals_and_distinguishes_pairs():
    def attr(pair, amount, cid="FX-CAMP-1"):
        return SimpleNamespace(campaign_id=cid, gain_loss=Decimal(amount), settlement=SimpleNamespace(instrument=pair, settlement_at=datetime(2026, 5, 1)))
    monthly = {
        (date(2026, 4, 1), date(2026, 4, 30)): (attr("USD/JPY", "25"), attr("USD/JPY", "-5"), attr("EUR/USD", "10")),
        (date(2026, 5, 1), date(2026, 5, 31)): (attr("USD/JPY", "7"),),
    }
    summaries, records = forex_drilldowns(monthly)
    april = [item for item in summaries if campaign_month(item) == "2026-04"]
    assert len(april) == 2
    assert sum(item.realized_pnl for item in april) == Decimal("30")
    assert len(summaries) == len(records) == 3
    assert all(item.record_count == len(records[item.campaign_id]) for item in summaries)
    # Reporting month follows persisted attribution, even with a next-month settlement date.
    assert all(campaign_month(item) == "2026-04" for item in april)


def test_overview_has_no_recent_campaigns_and_campaign_dropdown_is_removed():
    source = (Path(__file__).parents[1] / "src/campaigniq/ui/dashboard.py").read_text()
    overview = source.split('if view == "Overview":', 1)[1].split('if view == "Campaigns":', 1)[0]
    campaigns = source.split('if view == "Campaigns":', 1)[1].split('if view == "Positions":', 1)[0]
    assert "Recent Campaigns" not in overview
    assert "render_month_chart(" in overview
    assert '"Inspect campaign"' not in campaigns
    assert "render_campaign_table(" in campaigns
    assert '"Quantity closed"' in campaigns and '"Long/Short"' in campaigns
