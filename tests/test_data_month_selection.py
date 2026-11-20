from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from campaigniq.ui.account_month_selection import next_month,month_option
from tests.test_draft_display_snapshot import display,patches
from tests.test_data_draft_screen import listing

@pytest.mark.parametrize('periods,expected',[(('2026-10',),(2026,11)),(('2025-12',),(2026,1)),(('2025-04','2026-10','2026-02'),(2026,11)),((),(2026,11))])
def test_month_suggestion_from_history_not_wall_clock(periods,expected):
    assert next_month(periods,today=date(2026,11,20))==expected

def test_maximum_supported_month():
    with pytest.raises(ValueError):next_month(['2200-12'])

def test_status_label_preserves_id():
    d=SimpleNamespace(id='same',year=2026,month=11)
    assert month_option(d,False)=='November 2026 · Draft'
    assert month_option(d,True)=='November 2026 · Published' and d.id=='same'

def test_statuses_share_snapshot_without_other_result_replay(display):
    c=display;listing(c,[SimpleNamespace(id='older',year=2026,month=10,revision=7),c.draft])
    with patches(c),patch('campaigniq.account_deployment_application._published_session',side_effect=lambda service,session,account,draft,revision,**kw:draft.id=='older') as verified:
        r=c.app.draft_screen(c.actor,'account')
    assert r['selected']=='draft' and r['publication_statuses']=={'older':True,'draft':False}
    assert len(c.sessions)==1 and not c.sessions[0].active and c.locked==['draft'] and verified.call_count==2
    c.replay.assert_not_called()

def test_bad_other_receipt_withholds_screen(display):
    c=display;listing(c,[c.draft,SimpleNamespace(id='older',year=2026,month=10,revision=2)])
    with patches(c),patch('campaigniq.account_deployment_application._published_session',side_effect=[False,ValueError('receipt mismatch')]):
        with pytest.raises(ValueError,match='receipt mismatch'):c.app.draft_screen(c.actor,'account')
    assert len(c.sessions)==1 and not c.sessions[0].active

def month_screen():
    from streamlit.testing.v1 import AppTest
    from tests import test_monthly_review_cleanup as ui
    with patch.object(ui.AppTest,'from_string',side_effect=lambda source:SimpleNamespace(run=lambda **kw:source)):
        source=ui.screen()
    extra='''
service=Service()
values=(Draft(UUID(int=1),'scope',2026,10,0),Draft(UUID(int=2),'scope',2026,11,0))
service.drafts=lambda *a:values
service.published=lambda user,account,identifier,revision:identifier==values[0].id
overview=service.gateway.overview(USER)
def page():
    selected=st.session_state.get('signedin-input:registry-id:actor-id:account-id:draft',str(values[1].id))
    return dict(overview=overview,drafts=values,selected=selected,retained_months=('2025-01','2026-10'),
      publication_statuses={str(values[0].id):True,str(values[1].id):False},
      display=dict(published=selected==str(values[0].id),inventory=dict(protected=selected==str(values[0].id),documents=[]),no_trading=False))
def create(user,account,year,month):
    st.session_state.calls.append((account,year,month))
    return next(d for d in values if (d.year,d.month)==(year,month))
service.create=create
render(st,service,USER,deployment=True,selected_account_id='account-id',page_data=page())
'''
    return AppTest.from_string(source.replace('render(st,Service(),USER,deployment=True)',extra)).run(timeout=20)

def test_readable_defaults_saved_selection_no_write():
    app=month_screen();assert not app.exception and not app.error
    m=next(s for s in app.selectbox if s.label=='Month');assert m.value==11 and m.options[10]=='November'
    assert next(n for n in app.number_input if n.label=='Year').value==2026
    s=next(s for s in app.selectbox if s.label=='Saved month');assert s.options==['November 2026 · Draft','October 2026 · Published']
    s.select('00000000-0000-0000-0000-000000000001').run(timeout=20);app.run(timeout=20)
    assert not app.exception and not app.error
    assert next(s for s in app.selectbox if s.label=='Saved month').value.endswith('1')
    assert app.session_state.calls==[] and not app.file_uploader

def test_resume_exact_period_and_id():
    app=month_screen();next(s for s in app.selectbox if s.label=='Month').select(10)
    next(b for b in app.button if b.label=='Create or resume draft').click().run(timeout=20)
    assert not app.exception and not app.error
    assert list(app.session_state.calls)==[('account-id',2026,10)]
    assert next(s for s in app.selectbox if s.label=='Saved month').value=='00000000-0000-0000-0000-000000000001'
