from pathlib import Path

DASHBOARD = Path("src/campaigniq/ui/dashboard.py")

def test_dashboard_surfaces_repeated_campaign_performance() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    assert "from campaigniq.analytics.repeated_campaign_performance import (" in text
    assert "summarize_repeated_campaign_performance," in text
    assert "repeated_campaign_performance = summarize_repeated_campaign_performance(" in text
    assert 'st.subheader("Repeated-Campaign Performance")' in text
    assert '"First Realized Campaign": summary.first_campaign_id' in text
    assert '"First Realized Date": summary.first_realized_date' in text
    assert '"First Campaign P&L": float(summary.first_campaign_pnl)' in text
    assert '"Subsequent Realized P&L": float(summary.subsequent_realized_pnl)' in text
    assert "float(summary.subsequent_win_rate) * 100" in text
    assert "realized-campaign chronology, not true campaign-start chronology" in text
    assert "underlyings with at least two qualifying campaigns are shown" in text
    assert "Rows are sorted by subsequent realized P&L, worst first" in text
