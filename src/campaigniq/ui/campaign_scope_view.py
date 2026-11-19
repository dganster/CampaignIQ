"""Explain existing campaign scopes without recalculating their results."""


def render_metric_scope(ui, summary):
    ui.caption(
        f"Equity/options win rate: {summary.winning_campaign_count:,} winning / "
        f"{summary.campaign_count:,} included campaign-month results; "
        f"{summary.losing_campaign_count:,} losing and {summary.breakeven_campaign_count:,} breakeven. "
        f"{summary.excluded_campaign_count:,} campaign-month results failed recorded close checks and are excluded. "
        "Breakevens remain in the denominator. The same campaign can count in more than one month. "
        "These are realized results, not a count of fully closed campaigns. Forex, futures, and crypto are excluded from these metrics. "
        "Records without one unambiguous assigned campaign do not enter these counts."
    )


def render_list_scope(ui, *, equity_rows, forex_rows):
    ui.caption(
        f"Available drilldown rows before table filters: {equity_rows:,} equity/options and "
        f"{forex_rows:,} Forex. The list includes rows needing close-check review; "
        "it has a different scope from the equity/options win-rate denominator. "
        "Each row describes realized activity in a reporting month, not proof the whole campaign is closed."
    )
    ui.caption(
        "Recorded close checks compare allocated equity/options basis and realized results. "
        "Forex rows represent saved settlement attribution. Neither establishes complete "
        "campaign history, known opening cost for every lot, or independent source verification."
    )
