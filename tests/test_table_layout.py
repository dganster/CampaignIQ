from datetime import date
from copy import deepcopy

import pandas as pd
import streamlit as st

from campaigniq.ui.table_layout import FIT_KEY, fitted_column_config, render_dataframe
from campaigniq.ui.campaign_interactions import render_campaign_table, render_performance_months
from tests.test_campaign_interactions import UI


def test_fit_uses_every_column_heading_and_formatted_value_without_mutating_configs():
    table = pd.DataFrame({"Symbol": ["ABC"], "Amount": [12345678.90], "Date": [date(2026, 9, 30)],
                          "Long column title": ["Short text"]})
    config = {"Amount": st.column_config.NumberColumn("Amount", format="$%0,.2f", width=50),
              "Date": st.column_config.DateColumn("Date", format="MMM D, YYYY")}
    before = deepcopy(config)
    fit = fitted_column_config(table, config)
    assert set(fit) == set(table.columns)
    assert fit["Amount"]["width"] >= len("$12,345,678.90") * 8 + 36
    assert fit["Long column title"]["width"] >= len("Long column title") * 8 + 36
    assert fit["Date"]["type_config"] == config["Date"]["type_config"]
    assert config == before and table["Amount"].dtype.kind == "f"


def test_fit_mode_rebuilds_campaign_table_and_keeps_row_callback_correct():
    ui = UI(); table = pd.DataFrame({"Campaign": ["A", "B"], "Realized P&L": [-1000., 1200.]})
    render_campaign_table(ui, table, ("A", "B"), {})
    original_key = ui.calls[-1][1]["key"]
    ui.session_state[FIT_KEY] = True
    render_campaign_table(ui, table, ("A", "B"), {})
    styled, options = ui.calls[-1]
    assert options["key"] != original_key
    assert "width" in options["column_config"]["Campaign"]
    assert styled.data["Realized P&L"].dtype.kind == "f"
    ui.session_state[options["key"]] = {"selection": {"rows": [1]}}
    options["on_select"]()
    assert ui.session_state["campaigniq_campaign_detail"] == "B"


def test_month_selection_after_fit_still_opens_correct_reporting_month():
    ui = UI(); ui.session_state[FIT_KEY] = True
    table = pd.DataFrame({"Month": [date(2026, 1, 1), date(2026, 2, 1)]})
    render_performance_months(ui, table, ("2026-01", "2026-02"), {})
    options = ui.calls[-1][1]
    ui.session_state[options["key"]] = {"selection": {"rows": [1]}}
    options["on_select"]()
    assert ui.session_state["campaigniq_campaign_month"] == "2026-02"
    assert ui.session_state["campaigniq_primary_view"] == "Campaigns"


def test_default_mode_preserves_table_options_and_exact_fractional_strings():
    ui = UI(); values = [{"Quantity": "0.00000057"}]
    render_dataframe(ui, values, hide_index=True, use_container_width=True)
    assert ui.calls[-1] == (values, {"hide_index": True, "use_container_width": True})
    ui.session_state[FIT_KEY] = True
    render_dataframe(ui, values, hide_index=True)
    assert ui.calls[-1][0] == values
    assert ui.calls[-1][1]["column_config"]["Quantity"]["width"] >= 10 * 8 + 36
