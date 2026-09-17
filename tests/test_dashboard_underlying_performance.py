from pathlib import Path

DASHBOARD = Path("src/campaigniq/ui/dashboard.py")

def test_dashboard_surfaces_underlying_performance() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    assert "from campaigniq.analytics.underlying_performance import (" in text
    assert "summarize_underlying_performance," in text
    assert "underlying_performance = summarize_underlying_performance(" in text
    assert 'st.subheader("Underlying Performance")' in text
    assert '"Underlying": summary.underlying' in text
    assert '"Realized P&L": float(summary.realized_pnl)' in text
    assert '"Campaigns": summary.campaign_count' in text
    assert '"Win Rate": float(summary.win_rate) * 100' in text
    assert '"Average Campaign P&L": float(summary.average_campaign_pnl)' in text
    assert '"Median Campaign P&L": float(summary.median_campaign_pnl)' in text
    assert '"Best Campaign": summary.best_campaign_id' in text
    assert '"Worst Campaign": summary.worst_campaign_id' in text
    assert "Equity/options only." in text
    assert "FOREX is excluded" in text
