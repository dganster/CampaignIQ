from types import SimpleNamespace
from unittest.mock import Mock
from campaigniq.ui.account_forex_financial_policy_view import render
from tests.test_forex_inventory_view import DailySourceScreen


def setup():
    st=DailySourceScreen();st.upload=None;st.button_name='Calculate saved Forex financial candidate'
    calc=dict(totals={'trading_usd':'-10.00'},closes=[{'reference':'1001','candidate_pl_usd':'-10.00'}],
        last_independent_daily_boundary='2026-10-07',all_original_financing_rows_classified=True,limitations=['No publication.'])
    proposal=dict(artifact={'calculation':calc},artifact_sha256='a'*64)
    service=SimpleNamespace(forex_financial_candidate_preview=Mock(return_value=proposal),
        save_forex_financial_policy=Mock(),forex_financial_policy_preview=Mock(return_value=dict(
            candidate=proposal,receipt={'confirmed_by':'owner'},publication_blocker='Writer integration pending.')))
    return st,service


def draw(st,service,*,user='owner',account='account',revision=5,**kwargs):
    render(st,service,user,account,SimpleNamespace(id='draft',revision=revision),key='scope',**kwargs)


def test_explicit_monetary_choices_save_only_displayed_review_and_clear_it():
    st,s=setup();draw(st,s)
    s.save_forex_financial_policy.assert_not_called()
    st.button_name='Approve Forex monetary basis';draw(st,s)
    s.save_forex_financial_policy.assert_called_once_with('owner','account','draft',5,
        reviewed_candidate_sha256='a'*64,confirm_daily_basis=True,confirm_retained_differences=True,confirm_coverage_limits=True)
    assert st.reruns==1 and not st.session_state


def test_review_displays_cutoff_and_original_cash_differences_before_approval():
    st,s=setup();artifact=s.forex_financial_candidate_preview.return_value['artifact']
    artifact['calculation'].update(calendar_comparison={'prior_calendar_financing_in_daily_sessions_usd':'-1.00',
        'derived_opening_minus_export_usd':'0.03'},raw_csv_controls={'mtd_settled_pl_usd':'-9.00'},
        original_csv_mtd_minus_candidate_usd='1.00')
    artifact['original_cash_source_differences']={'saved_history_cash_differences':[dict(reference='1001',reported='-10',balance_change='-9.97')]}
    draw(st,s)
    assert any(r.get('Measure')=='Prior-calendar financing included in daily sessions' for table in st.tables for r in table)
    assert any(r.get('Measure')=='Original CSV MTD P/L minus candidate' for table in st.tables for r in table)
    assert any(r.get('Running-balance change, USD')=='-9.97' for table in st.tables for r in table)
    s.save_forex_financial_policy.assert_not_called()


def test_unchecked_choice_or_unclassified_rows_cannot_approve():
    for unclassified in (False,True):
        st,s=setup()
        if unclassified:s.forex_financial_candidate_preview.return_value['artifact']['calculation']['all_original_financing_rows_classified']=False
        else:st.checkbox=lambda *a,**k:False
        draw(st,s);st.button_name='Approve Forex monetary basis';draw(st,s)
        s.save_forex_financial_policy.assert_not_called()


def test_actor_account_or_revision_change_discards_review_and_approval_controls():
    for kwargs in ({'user':'other'},{'account':'foreign'},{'revision':6}):
        st,s=setup();draw(st,s);st.button_name='Approve Forex monetary basis';st.tables=[]
        draw(st,s,**kwargs)
        s.save_forex_financial_policy.assert_not_called()
        assert not st.tables and not st.session_state


def test_failed_fresh_review_or_save_cannot_reuse_stale_review():
    for save in (False,True):
        st,s=setup();draw(st,s)
        if save:st.button_name='Approve Forex monetary basis';s.save_forex_financial_policy.side_effect=ValueError('Changed sources')
        else:s.forex_financial_candidate_preview.side_effect=ValueError('Changed sources')
        draw(st,s)
        assert not st.session_state and st.errors==['Changed sources']


def test_view_only_and_published_can_read_saved_approval_but_never_approve():
    for kwargs in ({'writable':False},{'published':True}):
        st,s=setup();draw(st,s,**kwargs)
        st.button_name='Approve Forex monetary basis';draw(st,s,**kwargs)
        s.save_forex_financial_policy.assert_not_called()
        st.button_name='View saved Forex monetary approval';draw(st,s,**kwargs)
        s.forex_financial_policy_preview.assert_called_once_with('owner','account','draft',5)
        assert 'Writer integration pending.' in st.warnings
