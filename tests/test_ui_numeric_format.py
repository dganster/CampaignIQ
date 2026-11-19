from decimal import Decimal

import pandas as pd
import pytest

from campaigniq.ui.financial_format import (
    display_exact_money, display_money, display_quantity, render_numeric_table,
)


@pytest.mark.parametrize('value,expected', [
    ('2000', '2,000'), ('-2000', '-2,000'),
    ('0.03025223', '0.03025223'), ('0.00000059', '0.00000059'),
    ('1234.5000', '1,234.5'), ('-0.000', '0'),
])
def test_quantity_preserves_precision(value, expected):
    assert display_quantity(Decimal(value)) == expected


@pytest.mark.parametrize('formatter', [display_money, display_exact_money, display_quantity])
@pytest.mark.parametrize('value', [None, float('nan'), Decimal('NaN')])
def test_missing_values_are_unavailable(formatter, value):
    assert formatter(value) == 'Unavailable'


def test_execution_price_retains_source_precision():
    assert display_exact_money(Decimal('82293.92735')) == '$82,293.92735'
    assert display_exact_money(Decimal('-80574.47091')) == '-$80,574.47091'
    assert display_exact_money(Decimal('0')) == '$0.00'
    assert display_money(Decimal('-3108.75')) == '-$3,108.75'


def test_styled_table_keeps_numeric_values_order_and_source():
    class UI:
        session_state = {}

        def dataframe(self, data, **kwargs):
            self.table = data

    original = pd.DataFrame({'Quantity': [2000, -2], 'Basis': [2500.0, None]})
    before = original.copy(deep=True)
    ui = UI()
    render_numeric_table(ui, original, quantities=('Quantity',), money=('Basis',))
    pd.testing.assert_frame_equal(original, before)
    pd.testing.assert_frame_equal(ui.table.data, before)
    html = ui.table.to_html()
    assert '2,000' in html
    assert '$2,500.00' in html
    assert 'Unavailable' in html


def test_crypto_display_preserves_evidence_and_qualifies_cash():
    from copy import deepcopy
    from campaigniq.ui.crypto_view import render_crypto_report

    class UI:
        session_state = {}

        def __init__(self):
            self.tables, self.messages = [], []

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
            return lambda *args, **kwargs: self.messages.append(args)

    fill = dict(executed_at='2026-10-15T10:17:57', pair='BTC/USD', side='BUY',
                quantity='0.00000059', price_usd='82293.92735',
                principal_usd='0.05', fee_usd='0', reference='1008312534574')
    report = dict(period_start='2026-10-01', period_end='2026-10-31',
                  fills=[fill, dict(fill, fee_usd=None)],
                  last_reported_cash_usd='0', cash_control_reconciled=False,
                  fill_control_reconciled=True, warnings=[], net_funding_usd='1996.25',
                  cash_ledger=[dict(amount_usd='-265.57', balance_usd=None,
                                    fee_cash_change_usd='-1.99',
                                    executed_at=fill['executed_at'], description='source text')])
    before = deepcopy(report)
    ui = UI()
    render_crypto_report(report, ui)
    assert report == before
    fills, ledger = ui.tables
    assert fills[0]['Quantity'] == '0.00000059'
    assert fills[0]['Price (USD)'] == '$82,293.92735'
    assert fills[0]['Reference'] == fill['reference']
    assert fills[0]['Source execution time'] == fill['executed_at']
    assert fills[0]['Fee (USD)'] == '$0.00'
    assert fills[1]['Fee (USD)'] == 'Unavailable'
    assert ledger[0]['Cash change (USD)'] == '-$265.57'
    assert ledger[0]['Reported balance (USD)'] == 'Unavailable'
    assert ledger[0]['Source date & time'] == fill['executed_at']
    assert ('Export-reported cash', '$0.00') in ui.messages
    assert any('Unverified closing balance' in str(message) for message in ui.messages)
