from pathlib import Path


DASHBOARD = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "campaigniq"
    / "ui"
    / "dashboard.py"
)


def dashboard_text() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def test_dashboard_imports_drawdown_analytics() -> None:
    text = dashboard_text()

    assert "summarize_period_qualified_realized_drawdown" in text
    assert "summarize_maximum_drawdown_contributions" in text


def test_dashboard_uses_monthly_attributions_for_drawdown() -> None:
    text = dashboard_text()

    assert (
        "realized_drawdown = "
        "summarize_period_qualified_realized_drawdown("
        in text
    )
    assert (
        "maximum_drawdown_contributions = "
        "summarize_maximum_drawdown_contributions("
        in text
    )


def test_dashboard_exposes_realized_drawdown_section() -> None:
    text = dashboard_text()

    assert 'st.subheader("Realized Drawdown")' in text
    assert '"Maximum Drawdown"' in text
    assert '"Current Drawdown"' in text
    assert '"Drawdown Peak"' in text
    assert '"Drawdown Trough"' in text


def test_dashboard_exposes_equity_and_drawdown_charts() -> None:
    text = dashboard_text()

    assert '"cumulative_pnl:Q"' in text
    assert '"running_peak_pnl:Q"' in text
    assert '"drawdown:Q"' in text
    assert "Realized equity curve and high-water mark" in text
    assert "Realized drawdown from high-water mark" in text


def test_dashboard_exposes_drawdown_contribution_table() -> None:
    text = dashboard_text()

    assert 'st.subheader("Maximum Drawdown Contribution")' in text
    assert '"Gross Gain"' in text
    assert '"Gross Loss"' in text
    assert '"Net P&L"' in text
    assert '"Largest Loss"' in text
    assert '"Contribution"' in text


def test_dashboard_documents_drawdown_scope() -> None:
    text = dashboard_text()

    assert "Unassigned, ambiguous, and " in text
    assert "unreconciled campaign provenance remains included." in text
    assert "FOREX is excluded." in text
