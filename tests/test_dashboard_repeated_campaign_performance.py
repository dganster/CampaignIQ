from pathlib import Path


def test_campaign_analysis_uses_one_sortable_underlying_table():
    text = Path("src/campaigniq/ui/dashboard.py").read_text(encoding="utf-8")
    assert 'st.subheader("Underlying Performance")' in text
    assert '"Campaigns": summary.campaign_count' in text
    assert 'st.subheader("Repeated-Campaign Performance")' not in text
    assert 'repeated_campaign_rows' not in text
    assert 'click column headers to re-sort the table' in text
