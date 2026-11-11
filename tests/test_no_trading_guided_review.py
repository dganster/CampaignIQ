from pathlib import Path
from streamlit.testing.v1 import AppTest
from tests.test_monthly_review_cleanup import screen as base_screen


def screen(*,saved=True):
    base=base_screen(saved=saved)
    code=Path(base._script_path).read_text()
    code=code.replace('def no_trading_status(self,*args):return False',"def no_trading_status(self,*args):return st.session_state.get('confirmed',False)")
    old="    def no_trading_preview(self,*args):return dict(owner_confirmed=False,review=dict(financials={}))"
    new='''    def no_trading_preview(self,*args):
        st.session_state.calls.append('statement-review')
        if st.session_state.get('reject_review',False):raise ValueError('Statement changed; review current sources')
        if not SAVED:raise ValueError('Upload the monthly brokerage statement first')
        return dict(owner_confirmed=st.session_state.get('confirmed',False),review_sha256='a'*64,
            review=dict(financials=dict(realized_trading_pnl_usd='0.00',income_usd='59.00',opening_cash_usd='8963.90',ending_cash_usd='9022.90',cash_income=[dict(symbol='DG',amount_usd='59.00')])))
    def save_no_trading_confirmation(self,user,account,draft,revision,**kwargs):
        assert kwargs==dict(reviewed_sha256='a'*64,confirm_no_trading=True,confirm_cash_income=True)
        assert revision==st.session_state.get('source_revision',0)
        st.session_state.confirmed=True
        st.session_state.source_revision=revision+1
        st.session_state.calls.append('owner-confirmation')
'''.replace('SAVED',str(saved))
    assert old in code;code=code.replace(old,new)
    code=code.replace("        st.session_state.calls.append('publish')","        if st.session_state.get('fail_publication',False):raise ValueError('Publication failed; nothing committed')\n        st.session_state.calls.append('publish')")
    return AppTest.from_string(code).run(timeout=15)


def button(at,label):return next(b for b in at.button if b.label==label)


def no_trading(at):at.radio[0].set_value('No trading activity').run();return at


def confirm(at):
    next(c for c in at.checkbox if c.label.startswith('I confirm no trading')).check().run()
    next(c for c in at.checkbox if c.label.startswith('I reviewed the separate')).check().run()
    button(at,'Confirm reviewed month').click().run()


def test_one_review_button_missing_statement_stays_blocked():
    at=no_trading(screen(saved=False))
    assert sum(b.label=='Review month' for b in at.button)==1
    assert not any(b.label=='Review statement without trading' for b in at.button)
    button(at,'Review month').click().run()
    assert not at.exception and any('brokerage statement first' in e.value for e in at.error)
    assert button(at,'Publish month').disabled


def test_review_owner_confirm_and_publish_has_no_second_review_click():
    at=no_trading(screen());button(at,'Review month').click().run()
    assert not at.exception and not at.error
    assert button(at,'Confirm reviewed month').disabled and button(at,'Publish month').disabled
    confirm(at)
    assert not at.exception and not at.error
    assert not button(at,'Publish month').disabled
    assert sum(b.label=='Review month' for b in at.button)==1
    assert at.session_state['calls'].count('owner-confirmation')==1
    assert any('review confirmed' in m.value for m in at.success)
    # Owner approval and final account/month publication authorization are separate.
    assert not next(c for c in at.checkbox if c.label.startswith('I authorize')).value
    at.run();assert not button(at,'Publish month').disabled
    # Streamlit forms submit checkbox changes together with the submit button.
    next(c for c in at.checkbox if c.label.startswith('I authorize')).check()
    button(at,'Publish month').click().run()
    assert not at.exception and not at.error
    assert at.session_state['calls'].count('publish')==1


def test_changed_revision_or_permission_context_discards_unconfirmed_statement_review():
    for key in ('source_revision','revision'):
        at=no_trading(screen());button(at,'Review month').click().run()
        at.session_state[key]=1;at.run()
        assert not at.exception
        assert not any(b.label=='Confirm reviewed month' for b in at.button)
        assert button(at,'Publish month').disabled


def test_confirmed_receipt_is_revalidated_and_failed_review_cannot_enable_publication():
    at=no_trading(screen());button(at,'Review month').click().run();confirm(at)
    assert not button(at,'Publish month').disabled
    at.session_state['reject_review']=True;at.run()
    assert not at.exception and any('Statement changed' in e.value for e in at.error)
    assert button(at,'Publish month').disabled


def test_failed_publication_requires_review_again_without_repeating_owner_confirmation():
    at=no_trading(screen());button(at,'Review month').click().run();confirm(at)
    at.session_state['fail_publication']=True
    next(c for c in at.checkbox if c.label.startswith('I authorize')).check()
    button(at,'Publish month').click().run()
    assert any('Publication failed' in e.value for e in at.error)
    assert 'publish' not in at.session_state['calls']
    at.run();assert button(at,'Publish month').disabled
    button(at,'Review month').click().run()
    assert not button(at,'Publish month').disabled
    assert at.session_state['calls'].count('owner-confirmation')==1
