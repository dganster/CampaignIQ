from pathlib import Path
from streamlit.testing.v1 import AppTest
from tests.test_monthly_review_cleanup import screen,button
from tests.test_no_trading_guided_review import screen as no_trading_screen,no_trading


def test_empty_draft_has_quiet_document_status_and_no_missing_correction_controls():
    app=screen()
    assert any(c.value=='No source documents saved for this month.' for c in app.caption)
    assert not any('No source documents' in i.value for i in app.info)
    assert any('January 2025' in i.value and 'Choose whether' in i.value for i in app.info)
    assert not any('correct saved uploads above' in i.value.lower() for i in app.info)
    assert app.session_state.calls==[]


def test_unreviewed_trading_month_shows_named_next_step_without_backend_review():
    app=screen();app.radio[0].set_value('I traded').run()
    assert any(i.value=='Save the documents for January 2025, then select Review month.' for i in app.info)
    assert app.session_state.calls==[]


def test_failed_review_has_one_persistent_error_and_no_conflicting_generic_prompt():
    initial=screen()
    source=Path(initial._script_path).read_text().replace("        st.session_state.calls.append('unified-review')", "        st.session_state.calls.append('unified-review')\n        raise ValueError('Save the Trade History before reviewing a trading month')")
    app=AppTest.from_string(source).run(timeout=20);app.radio[0].set_value('I traded').run()
    button(app,'Review month').click().run()
    expected='January 2025: Save the Trade History before reviewing a trading month'
    assert not app.exception and [e.value for e in app.error]==[expected]
    assert not any('Save the documents' in i.value or 'before authorizing' in i.value for i in app.info)
    app.run()
    assert [e.value for e in app.error]==[expected] and app.session_state.calls==['unified-review']
    assert not any(c.label.startswith('I authorize') for c in app.checkbox)
    app.session_state.source_revision=1;app.run()
    assert not app.error
    assert any('Save the documents for January 2025' in i.value for i in app.info)


def test_missing_no_trading_statement_does_not_add_second_review_instruction():
    app=no_trading(no_trading_screen(saved=False));button(app,'Review month').click().run()
    assert not app.exception and len(app.error)==1
    assert 'brokerage statement first' in app.error[0].value
    assert not any('Save the Brokerage Statement, then review' in i.value for i in app.info)
    assert not any(c.label.startswith('I authorize') for c in app.checkbox)


def test_empty_no_trading_draft_does_not_point_to_absent_recovery_controls():
    app=screen();app.radio[0].set_value('No trading activity').run()
    assert not app.exception
    assert not any('remove mistaken' in c.value.lower() for c in app.caption)
    assert app.session_state.calls==[]
