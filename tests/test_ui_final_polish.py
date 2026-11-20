"""Regression coverage for final screenshot review findings."""
from streamlit.testing.v1 import AppTest
from tests.test_ui_visual_cleanup import app_code
PREFIX='account-dashboard:test-registry:owner:a:'

def screen(view='Overview'):
    app=AppTest.from_string(app_code()).run(timeout=25)
    if view!='Overview':next(r for r in app.radio if r.label=='Navigate').set_value(view).run(timeout=25)
    assert not app.exception
    return app

def test_shortcuts_reading_order_and_compact_zero_attention():
    app=screen()
    assert [b.label for b in app.sidebar.button if b.label in ('YTD','All available data','Q1','Q2','Q3','Q4')]==['All available data','YTD','Q1','Q2','Q3','Q4']
    assert not any(m.label=='Unattributed Broker Records' for m in app.metric)
    assert any('No attribution issues' in c.value for c in app.caption)

def test_monthly_currency_and_primary_columns():
    app=screen('Performance')
    text=next(c.value for c in app.caption if c.value.startswith('Asset breakdown'))
    assert r'\$850.15' in text and r'\$0.00' in text
    frame=next(d.value for d in app.dataframe if 'Combined Realized P&L' in d.value.columns)
    assert list(frame.columns[:4])==['Month','Combined Realized P&L','Campaigns','Win Rate']
    assert frame.iloc[0]['Combined Realized P&L']==850.15 and frame.iloc[0]['Campaigns']==2

def test_history_context_missing_money_and_original_quantities():
    app=screen('Campaigns')
    app.session_state[PREFIX+'campaigniq_campaign_detail']='2025-01/SAMPLE-SPY'
    app.run(timeout=25)
    assert not app.exception
    frame=next(d.value for d in app.dataframe if 'Selected campaign' in d.value.columns)
    assert not frame['Selected campaign'].any()
    assert set(frame['Realized P&L'])=={'Unavailable'}
    assert frame['Quantity change'].tolist()==[100,10]
    assert any('No retained trade rows are linked' in c.value for c in app.caption)
    assert any('different campaigns do not form one running balance' in c.value for c in app.caption)
    assert any('Selected reporting period: January 2025.' in c.value for c in app.caption)
    assert any('**Recorded close checks:** Passed' in m.value for m in app.markdown)

def test_basis_availability_and_numeric_quantity():
    app=screen('Positions')
    frame=next(d.value for d in app.dataframe if 'Recorded basis (USD)' in d.value.columns)
    assert frame['Recorded basis (USD)'].tolist()==['$10,000.00','Unavailable']
    assert frame['Quantity'].tolist()==[100,10]

def test_tail_counts_use_actual_eligible_results():
    app=screen('Performance');app.session_state[PREFIX+'campaigniq_performance_tab']='Campaign Analysis';app.run(timeout=25)
    assert not app.exception
    values={m.label:m.value for m in app.metric}
    assert values['Top 1 Winner']=='+$1,250.25' and values['Bottom 1 Loser']=='-$400.10'

def test_nonzero_drawdown_retains_contribution():
    code=app_code().replace("attribution('SAMPLE-MSFT',date(2025,1,15)","attribution('SAMPLE-MSFT',date(2025,1,16)")
    app=AppTest.from_string(code).run(timeout=25)
    next(r for r in app.radio if r.label=='Navigate').set_value('Performance').run(timeout=25)
    app.session_state[PREFIX+'campaigniq_performance_tab']='Drawdown';app.run(timeout=25)
    assert not app.exception
    assert next(m.value for m in app.metric if m.label=='Maximum Drawdown')=='-$400.10'
    assert any(h.value=='Maximum Drawdown Contribution' for h in app.subheader)
    assert any('Contribution' in d.value.columns and not d.value.empty for d in app.dataframe)
