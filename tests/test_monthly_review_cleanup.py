from copy import deepcopy
import pytest
from tests.test_account_deployment_publication import deployed,local,month,bound_context,context
from campaigniq.futures_monthly_preview import monthly_preview


def test_equities_only_combined_review_reconciles_original_without_futures_sources_or_writes(deployed):
    c=deployed;before=deepcopy(c.repositories[c.account].__dict__);financial=deepcopy(c.artifacts[c.account].rows)
    with c.bound.gateway.transaction(c.admin,c.account) as session:
        result=monthly_preview(c.bound,session,session.drafts.get(c.draft.id),forex_applicable=False,crypto_applicable=False,futures_applicable=False)
    assert result['all_source_controls_ready'] and result['futures'] is None
    assert result['source_lineage']['format']=='campaigniq.original_monthly_history_lineage'
    assert result['source_lineage']['original_preserved']
    assert c.repositories[c.account].__dict__==before and c.artifacts[c.account].rows==financial

@pytest.mark.parametrize('value',[None,1,'no'])
def test_market_choice_is_explicit(value):
    with pytest.raises(ValueError):monthly_preview(None,None,None,forex_applicable=False,crypto_applicable=False,futures_applicable=value)


def test_saved_futures_evidence_cannot_be_excluded_from_combined_review(deployed):
    c=deployed;repo=c.repositories[c.account]
    repo.upload(c.draft.id,'futures_empty_opening_confirmation_v1','confirmation.json','text/plain',b'{}')
    before=deepcopy(repo.__dict__)
    with c.bound.gateway.transaction(c.admin,c.account) as session:
        with pytest.raises(ValueError,match='require Futures'):monthly_preview(c.bound,session,session.drafts.get(c.draft.id),forex_applicable=False,crypto_applicable=False,futures_applicable=False)
    assert c.repositories[c.account].__dict__==before

from pathlib import Path
from streamlit.testing.v1 import AppTest


def screen(*,published=False,saved=False):
    text=Path(__file__).with_name('test_account_signedin_inputs_view.py').read_text()
    code=text.split("    code = '''",1)[1].split("'''",1)[0]
    for key,value in (('READY',True),('WRITABLE',True),('ADMIN',True),('DRAFT',True),('ACCOUNTS',True),('MISMATCH',False),('PUBLISHED',published)):
        code=code.replace(key,str(value))
    code=code.replace('2025,1,0),)','2025,1,st.session_state.get("source_revision",0)),)')
    methods='''class Service:
    def saved_documents(self,*args):
        row=dict(id='source',role='schwab_brokerage_statement',purpose='Brokerage statement',filename='Statement.pdf',superseded=False)
        return dict(protected=PUBLISHED_VALUE,documents=[row] if SAVED_VALUE else [])
    def no_trading_status(self,*args):return False
    def no_trading_preview(self,*args):return dict(owner_confirmed=False,review=dict(financials={}))
    def monthly_source_preview(self,user,account,draft,revision,**kwargs):
        assert user is USER and account=='account-id'
        st.session_state.calls.append('unified-review')
        return dict(all_source_controls_ready=SAVED_VALUE,validations=[dict(role='Brokerage statement',valid=SAVED_VALUE,message='Statement source check')],source_findings=[])
'''.replace('PUBLISHED_VALUE',str(published)).replace('SAVED_VALUE',str(saved))
    code=code.replace('class Service:\n',methods)
    code=code.replace("kwargs['authorize_local_publication']", "kwargs.get('confirm_publication',False)")
    code=code.replace("brokerage_realized_usd='0',realized=()","brokerage_realized_usd='0',forex_settled_usd='0',futures_realized_usd='0',forex_policy_approved=False,realized=()")
    code=code.replace('render(st,Service(),USER)','render(st,Service(),USER,deployment=True)')
    return AppTest.from_string(code).run(timeout=15)


def button(at,label):return next(b for b in at.button if b.label==label)


def test_empty_equities_draft_has_one_review_and_cannot_publish():
    at=screen();at.radio[0].set_value('I traded').run()
    assert not at.exception and not at.error
    assert button(at,'Publish month').disabled
    assert not any('including futures' in e.label for e in at.expander)
    assert not any(b.label.startswith('Preview ') for b in at.button)
    button(at,'Review month').click().run()
    assert not at.exception and not at.error and button(at,'Publish month').disabled
    assert at.session_state['calls']==['unified-review']


def test_successful_review_survives_confirmation_rerun_but_market_and_revision_changes_invalidate():
    at=screen(saved=True);at.radio[0].set_value('I traded').run()
    button(at,'Review month').click().run();assert not button(at,'Publish month').disabled
    authorize=next(c for c in at.checkbox if c.label.startswith('I authorize'))
    authorize.check().run();assert not button(at,'Publish month').disabled
    at.multiselect[0].set_value(['Equities and options','Forex']).run()
    assert button(at,'Publish month').disabled
    at.multiselect[0].set_value(['Equities and options']).run();button(at,'Review month').click().run()
    assert not button(at,'Publish month').disabled
    at.session_state['source_revision']=1;at.run()
    assert button(at,'Publish month').disabled


def test_published_month_shows_documents_and_results_without_edit_or_review_workflow():
    at=screen(published=True,saved=True)
    assert not at.exception and not at.radio and not at.multiselect and not at.file_uploader
    assert not any(b.label in ('Review month','Publish month','Review removal') for b in at.button)
    button(at,'View published results').click().run()
    assert not at.exception and not at.error


def test_no_trading_auto_selects_statement_and_only_saved_role_shows_replacement():
    at=screen();at.radio[0].set_value('No trading activity').run()
    assert not any(s.label=='Document role' for s in at.selectbox)
    assert not any('Replace the saved' in c.label for c in at.checkbox)
    saved=screen(saved=True);saved.radio[0].set_value('No trading activity').run()
    assert any('Replace the saved' in c.label for c in saved.checkbox)
    settings=next(e for e in saved.expander if e.label=='Account settings')
    assert settings.proto.expanded # Synthetic test account has no bound suffix yet.
