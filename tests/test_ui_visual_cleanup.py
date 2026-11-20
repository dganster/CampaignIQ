"""Exercise report presentation with real Streamlit composition and unchanged figures."""
from streamlit.testing.v1 import AppTest
from tests.test_account_dashboard_view import app_code as empty_app_code

def app_code():
    return empty_app_code().replace("        return ReportSnapshot(account_id,_candidate('12345678',double=account_id=='b'))", '''        from datetime import date
        from decimal import Decimal as D
        from tests.test_campaign_economics import attribution
        from campaigniq.persistence.realized_attribution_store import serialize_realized_attributions
        rows=dict(_candidate('12345678',double=account_id=='b'))
        rows['2025-01-realized-attributions.json']=serialize_realized_attributions(period_start=date(2025,1,1),period_end=date(2025,1,31),attributions=(attribution('SAMPLE-SPY',date(2025,1,15),'1250.25',symbol='SPY'),attribution('SAMPLE-MSFT',date(2025,1,15),'-400.10',symbol='MSFT')))
        return ReportSnapshot(account_id,rows)''')


def test_single_month_overview_has_compact_scope_and_actionable_attention():
    app=AppTest.from_string(app_code()).run(timeout=25)
    assert not app.exception
    assert any('winning /' in c.value for c in app.caption)
    assert any(e.label == 'How campaign win rate is counted' and not e.proto.expanded for e in app.expander)
    assert not any(m.label == 'Data Through' for m in app.metric)
    assert any(m.label == 'Worst Campaign' for m in app.metric)
    assert any(b.label == 'All available data' for b in app.button)


def test_selected_campaign_retains_accounting_evidence_in_detail_tabs():
    app=AppTest.from_string(app_code()).run(timeout=25)
    next(r for r in app.radio if r.label=='Navigate').set_value('Campaigns').run(timeout=25)
    # Use the same selection callback event as a table click.
    import json
    tables=[d for d in app.dataframe if 'Campaign' in d.value.columns]
    assert tables
    table=tables[0]
    app.session_state['account-dashboard:test-registry:owner:a:campaigniq_campaign_detail']=table.value.iloc[0]['Campaign']
    app.run(timeout=25)
    assert not app.exception
    assert {'Summary','History','Accounting'} <= {t.label for t in app.tabs}
    assert any('Recorded close checks' in m.value for m in app.markdown)
    assert not any(m.label in ('First close','Last close') for m in app.metric)


def test_zero_drawdown_does_not_render_empty_contribution_grid():
    app=AppTest.from_string(app_code()).run(timeout=25)
    next(r for r in app.radio if r.label=='Navigate').set_value('Performance').run(timeout=25)
    app.session_state['account-dashboard:test-registry:owner:a:campaigniq_performance_tab']='Drawdown'
    app.run(timeout=25)
    assert not app.exception
    assert any(m.label=='Maximum Drawdown' and m.value=='$0.00' for m in app.metric)
    assert not any(d.value.empty for d in app.dataframe)
    assert not any(m.label in ('Drawdown Peak','Drawdown Trough') for m in app.metric)


def test_single_winner_concentration_has_one_comparison_and_full_campaign_values():
    app=AppTest.from_string(app_code()).run(timeout=25)
    next(r for r in app.radio if r.label=='Navigate').set_value('Performance').run(timeout=25)
    app.session_state['account-dashboard:test-registry:owner:a:campaigniq_performance_tab']='Campaign Analysis'
    app.run(timeout=25)
    assert not app.exception
    comparison=next(d.value for d in app.dataframe if 'Winning campaigns' in d.value.columns)
    assert len(comparison)==1
    assert comparison.iloc[0]['Available winners included']==1
    assert comparison.iloc[0]['Realized P&L']==1250.25
    assert comparison.iloc[0]['Net without these winners']==-400.10
    worst=next(m for m in app.metric if m.label=='Worst Campaign')
    assert worst.value=='-$400.10' and not worst.delta
