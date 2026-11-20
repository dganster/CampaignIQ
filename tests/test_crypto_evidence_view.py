from copy import deepcopy

from campaigniq.ui.crypto_evidence_view import has_statement_choices, render_crypto_limitations
from campaigniq.ui.crypto_view import render_crypto_report


class UI:
    session_state = {}

    def __init__(self):
        self.messages, self.tables = [], []

    def columns(self, count):
        return [self] * count

    def dataframe(self, rows, **kwargs):
        self.tables.append(rows)

    def expander(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __getattr__(self, name):
        return lambda *args, **kwargs: self.messages.append((name, args))


def report(approved=False):
    value = dict(period_start='2026-10-01', period_end='2026-10-31',
                 fills=[], cash_ledger=[], net_funding_usd='1996.25',
                 last_reported_cash_usd='0', cash_control_reconciled=False,
                 fill_control_reconciled=True, status='provisional export evidence',
                 warnings=['Crypto cash running-balance control is incomplete or mismatched.',
                           'Opening crypto quantities are inferred from this export; no previous published crypto state is available.',
                           'Unrecognized source limitation remains visible.'])
    if approved:
        value['status'] = 'owner-approved statement sources; original export discrepancies retained'
        value['approved_statement_financials'] = dict(
            format='campaigniq.approved_crypto_financial_candidate', calculation={},
            approval=dict(source_receipt_verified=True, account_association='owner_confirmed', broker_verified_account_link=False))
    return value


def test_approved_figures_and_original_controls_are_distinguished_without_mutation():
    data = report(True)
    before = deepcopy(data)
    ui = UI()
    render_crypto_report(data, ui)
    assert data == before
    assert ('metric', ('Statement closing cash', '$0.00')) in ui.messages
    assert not any(name == 'success' for name, _ in ui.messages)
    warnings = [args[0] for name, args in ui.messages if name == 'warning']
    assert len(warnings) == 1
    assert warnings[0].count('cash running balances are incomplete') == 1
    assert 'Original export calculation: opening units were inferred' in warnings[0]
    assert 'Unrecognized source limitation remains visible.' in warnings[0]
    text = str(ui.messages)
    assert 'Opening units use the preceding statement' in text
    assert 'Owner approval is separate from independent source reconciliation' in text
    assert 'Individual fills, recorded fees, execution prices' in text


def test_export_only_zero_cash_is_not_labeled_statement_cash():
    ui = UI()
    render_crypto_report(report(), ui)
    assert ('metric', ('Export-reported cash', '$0.00')) in ui.messages
    assert 'Unverified closing balance' in str(ui.messages)
    assert 'No owner-approved statement financial record' in str(ui.messages)


def test_summary_does_not_invent_per_field_verification():
    ui = UI()
    warnings = ('Crypto holdings are an undated export snapshot, not an independently verified month-end statement.',) * 2
    render_crypto_limitations(ui, status='owner-approved statement sources; original export discrepancies retained', warnings=warnings)
    shown = [args[0] for name, args in ui.messages if name == 'warning']
    assert len(shown) == 1 and shown[0].count('no independently verified month-end date') == 1
    assert 'does not include per-field source controls' in str(ui.messages)
    assert not any(name == 'success' for name, _ in ui.messages)


def test_missing_approval_is_not_treated_as_statement_choice():
    data = report(True)
    data['approved_statement_financials']['approval']['source_receipt_verified'] = False
    assert not has_statement_choices(data)


def test_snapshot_difference_remains_in_visible_panel():
    data = report(True)
    data['snapshot_mismatches'] = [dict(pair='BTC/USD', computed='0.03025223', snapshot='0.03')]
    ui = UI()
    render_crypto_limitations(ui, report=data, warnings=data['warnings'])
    assert any(name == 'warning' and 'computed quantities differ' in args[0] for name, args in ui.messages)


def test_matching_exports_do_not_imply_month_end_verification():
    data = report()
    data.update(cash_control_reconciled=True, fill_control_reconciled=True, warnings=[])
    ui = UI()
    render_crypto_limitations(ui, report=data)
    assert 'do not by themselves verify month-end holdings' in str(ui.messages)
    assert not any(name == 'success' for name, _ in ui.messages)


def test_real_crypto_screen_shows_one_panel_and_exact_quantity():
    from streamlit.testing.v1 import AppTest
    data = report(True)
    data.update(ending_quantities={'BTC/USD': '0.03025223'},
                holdings_snapshot={'BTC/USD': {'quantity': '0.03025223'}},
                ending_lots={'BTC/USD': [{'cost_usd': '2500.00'}]},
                opening_source='approved preceding statement; acquisition costs documented separately',
                realized_sales=[], snapshot_mismatches=[])
    code = 'import streamlit as st\nfrom campaigniq.ui.crypto_view import render_crypto_report\nrender_crypto_report(' + repr(data) + ', st)'
    app = AppTest.from_string(code).run(timeout=20)
    assert not app.exception and len(app.warning) == 1
    assert any(m.label == 'Statement closing cash' for m in app.metric)
    assert app.dataframe[0].value.iloc[0]['Computed quantity'] == '0.03025223'
    assert app.dataframe[0].value.iloc[0]['Remaining acquisition cost incl. fees'] == '$2,500.00'


def test_published_data_view_preserves_crypto_quantity_and_single_panel():
    from types import SimpleNamespace
    from campaigniq.ui.account_signedin_inputs_view import show_published_results
    data = report(True)
    result = SimpleNamespace(month='2026-10', revision=37, original_documents=5,
                             positions=(), lots=(), realized=(), brokerage_realized_usd='19020.47',
                             forex_settled_usd='1376.84', futures_realized_usd='-3793.60',
                             forex_applicable=False, crypto_applicable=True,
                             crypto_status=data['status'], crypto_warnings=tuple(data['warnings']),
                             crypto_positions=(('BTC/USD', '0.03025223'),), forex_policy_approved=False)
    ui = UI()
    show_published_results(ui, SimpleNamespace(results=lambda *args: result), object(), 'account',
                           SimpleNamespace(id='draft', revision=37), deployment=True)
    assert sum(name == 'warning' and 'Original export' in str(args) for name, args in ui.messages) == 1
    assert ui.tables[-1] == [{'Instrument': 'BTC/USD', 'Quantity': '0.03025223'}]
