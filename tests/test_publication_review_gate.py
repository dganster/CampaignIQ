from pathlib import Path
from streamlit.testing.v1 import AppTest
from tests.test_monthly_review_cleanup import screen,button


def authorization(app):
    return [c for c in app.checkbox if c.label.startswith('I authorize publication')]


def test_unreviewed_and_failed_review_hide_authorization_and_publish():
    app=screen();app.radio[0].set_value('I traded').run()
    assert not authorization(app) and not any(b.label=='Publish month' for b in app.button)
    button(app,'Review month').click().run()
    assert not app.exception and not authorization(app)
    assert not any(b.label=='Publish month' for b in app.button) and 'publish' not in app.session_state.calls


def test_successful_review_shows_named_month_without_publication():
    app=screen(saved=True);app.radio[0].set_value('I traded').run()
    button(app,'Review month').click().run()
    assert not app.exception and len(authorization(app))==1
    assert authorization(app)[0].label.endswith('January 2025')
    assert not authorization(app)[0].value and 'publish' not in app.session_state.calls


def test_failed_rereview_clears_prior_authorization_before_retry():
    initial=screen(saved=True)
    source=Path(initial._script_path).read_text().replace("        assert user is USER and account=='account-id'\n        st.session_state.calls.append('unified-review')", "        assert user is USER and account=='account-id'\n        if st.session_state.get('reject_review',False):raise ValueError('Source changed')\n        st.session_state.calls.append('unified-review')")
    app=AppTest.from_string(source).run(timeout=20);app.radio[0].set_value('I traded').run()
    button(app,'Review month').click().run();app.session_state[authorization(app)[0].key]=True;app.run()
    assert authorization(app)[0].value
    app.session_state.reject_review=True;button(app,'Review month').click().run()
    assert not app.exception and app.error and not authorization(app)
    assert not any(b.label=='Publish month' for b in app.button)
    app.session_state.reject_review=False;button(app,'Review month').click().run()
    assert len(authorization(app))==1 and not authorization(app)[0].value
    assert 'publish' not in app.session_state.calls


def test_source_directory_market_and_activity_changes_hide_authorization():
    for change in ('source_revision','revision','markets','activity'):
        app=screen(saved=True);app.radio[0].set_value('I traded').run()
        button(app,'Review month').click().run();app.session_state[authorization(app)[0].key]=True;app.run()
        if change=='markets':app.multiselect[0].set_value(['Equities and options','Forex']).run()
        elif change=='activity':app.radio[0].set_value('No trading activity').run()
        else:app.session_state[change]=1;app.run()
        assert not app.exception and not authorization(app)
        assert not any(b.label=='Publish month' for b in app.button)
        assert 'publish' not in app.session_state.calls
