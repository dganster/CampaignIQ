from pathlib import Path

import pandas as pd
import pytest

from campaigniq.ui.campaign_interactions import (
    apply_performance_typography, render_performance_months,
)


class UI:
    def __init__(self):
        self.session_state = {}

    def dataframe(self, table, **kwargs):
        self.table, self.kwargs = table, kwargs

    def html(self, html):
        self.html_content = html


def test_month_row_opens_exact_month_after_browser_sort_and_resets_filters():
    ui = UI()
    ui.session_state.update(campaigniq_campaign_symbol="CMI", campaigniq_campaign_result="Loss", campaigniq_campaign_detail="old")
    table = pd.DataFrame({"Month": ["March 2026", "April 2026"], "Combined Realized P&L": [10.0, -5.0]})
    render_performance_months(ui, table, ("2026-03", "2026-04"), {})
    assert ui.kwargs["selection_mode"] == "single-row"
    ui.session_state[ui.kwargs["key"]] = {"selection": {"rows": [1]}}
    ui.kwargs["on_select"]()
    assert ui.session_state["campaigniq_primary_view"] == "Campaigns"
    assert ui.session_state["campaigniq_campaign_month"] == "2026-04"
    assert ui.session_state["campaigniq_campaign_symbol"] == "All symbols"
    assert ui.session_state["campaigniq_campaign_result"] == "All results"
    assert ui.session_state["campaigniq_campaign_detail"] is None
    assert ui.table is table


@pytest.mark.parametrize("rows", [[], [-1], [99]])
def test_cleared_or_invalid_month_selection_does_not_navigate(rows):
    ui = UI()
    render_performance_months(ui, pd.DataFrame(), ("2026-04",), {})
    ui.session_state[ui.kwargs["key"]] = {"selection": {"rows": rows}}
    ui.kwargs["on_select"]()
    assert "campaigniq_primary_view" not in ui.session_state


def test_changed_month_order_has_fresh_selection_state():
    ui = UI()
    render_performance_months(ui, pd.DataFrame(), ("2026-03", "2026-04"), {})
    key = ui.kwargs["key"]
    render_performance_months(ui, pd.DataFrame(), ("2026-04", "2026-03"), {})
    assert ui.kwargs["key"] != key


def test_typography_is_applied_to_every_view():
    ui = UI()
    apply_performance_typography(ui)
    assert 'stMetricLabel' in ui.html_content
    assert 'font-weight: 600' in ui.html_content
    assert 'font-size: 1.65rem' in ui.html_content
    source = (Path(__file__).parents[1] / "src/campaigniq/ui/dashboard.py").read_text()
    assert source.index("apply_performance_typography(st)") < source.index("navigation_views =")
    assert 'if view == "Performance":\n    apply_performance_typography(st)' not in source
    assert "render_performance_months(" in source
