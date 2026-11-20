from types import SimpleNamespace
from streamlit.testing.v1 import AppTest
from campaigniq.ui.account_dashboard_view import account_labels


def account(identifier,name,archived=False):
    return SimpleNamespace(id=identifier,name=name,archived=archived)


def test_unique_account_names_have_no_internal_suffix():
    rows=[account('c8e9e6f2-private',"Dennis' Brokerage Account"),account('3d4a2c10-private',"Andrew's Brokerage Account")]
    assert account_labels(rows)=={r.id:r.name for r in rows}


def test_duplicate_names_have_readable_distinctions_and_stable_id_mapping():
    rows=[account('internal-b','Brokerage'),account('internal-a','Brokerage')]
    labels=account_labels(rows)
    assert labels=={'internal-a':'Brokerage · Account 1','internal-b':'Brokerage · Account 2'}
    assert account_labels(reversed(rows))==labels


def test_archived_status_and_generated_label_collisions_stay_distinct():
    rows=[account('a','Brokerage'),account('b','Brokerage'),account('c','Brokerage · Account 1'),account('d','Brokerage',True)]
    labels=account_labels(rows)
    assert len(set(v.casefold() for v in labels.values()))==4
    assert labels['c']=='Brokerage · Account 1' and labels['d']=='Brokerage [archived]'
    assert labels['a']=='Brokerage · Account 2' and labels['b']=='Brokerage · Account 3'


def test_sidebar_shows_name_and_routes_original_id_without_extra_choices_calls():
    # Keep the real sidebar and routing; substitute the expensive report renderer.
    code='''
import streamlit as st
from types import SimpleNamespace
from campaigniq.account_dashboard import DashboardAccount,DashboardChoices
import campaigniq.ui.account_dashboard_view as view
class User:
    def to_dict(self):return {'is_logged_in':True}
class UI:
    user=User()
    def __getattr__(self,name):return getattr(st,name)
class Service:
    store=SimpleNamespace(registry_id='registry')
    def choices(self,user):
        st.session_state['choice_calls']=st.session_state.get('choice_calls',0)+1
        return DashboardChoices('owner',False,(DashboardAccount('c8e9e6f2-private',"Dennis' Brokerage Account",False,False),DashboardAccount('3d4a2c10-private',"Andrew's Brokerage Account",False,False)))
    def snapshot(self,user,identifier,**kwargs):
        st.session_state['selected_id']=identifier
        return SimpleNamespace(list_keys=lambda **kwargs:[])
view.render(UI(),Service(),administration=None)
'''
    app=AppTest.from_string(code).run(timeout=20)
    assert not app.exception and not app.error
    selector=next(s for s in app.selectbox if s.label=='Brokerage account')
    assert selector.options==["Dennis' Brokerage Account","Andrew's Brokerage Account"]
    assert app.session_state['selected_id']=='c8e9e6f2-private' and app.session_state['choice_calls']==1
    selector.select('3d4a2c10-private').run(timeout=20)
    assert not app.exception and app.session_state['selected_id']=='3d4a2c10-private'
    assert app.session_state['choice_calls']==2
