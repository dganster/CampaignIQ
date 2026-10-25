"""Candidates require exact executions and independent position boundaries."""
from decimal import Decimal as D
import csv
import io

import pytest
from tests.test_forex_monetary_audit import evidence as monetary_evidence
import campaigniq.forex_import_preview as module


@pytest.fixture
def inputs(monetary_evidence, tmp_path):
    args = dict(monetary_evidence)
    summary, detail = args['monthly_text'].split('\f')
    header = summary.splitlines()[0].replace('August 29, 2025', 'August 1, 2025')
    first_day = '\n'.join([header, 'Open Position(s)',
        'EUR/USD - TOTAL POSITION: Sell (50,000) $0.00 $0.00',
        'Closed Position(s)', 'Trade Activity Detail', 'Financing Report', 'Conversion Report'])
    args['monthly_text'] = summary + '\f' + first_day + '\f' + detail
    output = io.StringIO(); w = csv.writer(output)
    for row in [
        ['Transaction Report since Jun 30, 2025 16:00:00 (EDT)  through Jul 31, 2025 16:00:00 (EDT)'],
        ['Account (USD): 1234567, | Base currency: (USD)'],
        ['MTD Settled PL, USD:', '0.00'], ['MTD fee, USD:', '0.00'],
        ['="11111"', 'Jul 31, 2025 11:44:25', 'Jul 31, 2025 17:00:00', 'new',
         'EUR/USD', 'Sell', '1.14331', '-50000', '57165.50', '0.00', '1.14331', '', '-50000'],
    ]:
        w.writerow(row)
    path = tmp_path / 'opening.csv'; path.write_text(output.getvalue())
    args['opening_transaction_report'] = path
    return args


def preview(inputs, **overrides):
    sources = [inputs[k] for k in ['transaction_report', 'trade_history', 'opening_transaction_report']]
    before = [p.read_bytes() for p in sources]
    result = module.build_forex_import_preview(**dict(inputs, **overrides))
    assert before == [p.read_bytes() for p in sources]
    return result


def test_verified_candidate_replays_from_corroborated_opening_to_statement_close(inputs):
    result = preview(inputs)
    assert result['candidate_inputs_verified'] is True
    assert result['opening_positions_corroborated'] is True
    assert result['closing_quantities_reconciled'] is True
    assert result['closing_quantities'] == {}
    assert result['opening_position_candidates'][0]['reference'] == '11111'
    row, = result['execution_candidates']
    assert row['reference'] == '12345' and row['rate'] == '1.166891'
    assert row['position_effect'] == 'CLOSE'
    assert result['candidate_settled_pl_usd'] == '-10.00'
    assert result['candidate_financing_usd'] == '1.00'


def test_preview_does_not_authorize_policy_import_campaigns_or_publication(inputs):
    result = preview(inputs)
    assert result['preview_only'] is True
    for field in ['import_authorized', 'publication_authorized', 'canonical_campaigns_verified', 'full_continuity_verified']:
        assert result[field] is False
    assert result['execution_candidates'][0]['canonical_campaign_id'] is None
    assert result['original_csv_monetary_audit']['csv_ready_for_import'] is False


def test_missing_csv_execution_stays_visible_while_corroborated_candidate_is_available(inputs):
    p = inputs['transaction_report']
    p.write_text('\n'.join(line for line in p.read_text().splitlines() if ',settlement,' not in line))
    result = preview(inputs)
    assert len(result['execution_candidates']) == 1
    assert len(result['original_csv_monetary_audit']['missing_csv_closes']) == 1
    assert not result['import_authorized']


def test_mismatching_opening_quantity_blocks(inputs):
    text = inputs['monthly_text'].replace('Sell (50,000)', 'Sell (49,999)')
    with pytest.raises(ValueError, match='Opening report quantities disagree'):
        preview(inputs, monthly_text=text)


def test_opposite_opening_side_blocks(inputs):
    text = inputs['monthly_text'].replace('Sell (50,000)', 'Buy 50,000')
    with pytest.raises(ValueError, match='Opening report quantities disagree'):
        preview(inputs, monthly_text=text)


def test_missing_daily_corroboration_blocks(inputs):
    summary, _, detail = inputs['monthly_text'].split('\f')
    with pytest.raises(ValueError, match='first daily'):
        preview(inputs, monthly_text=summary + '\f' + detail)


def test_execution_before_first_daily_snapshot_requires_better_boundary_evidence(inputs):
    p = inputs['trade_history']; p.write_text(p.read_text().replace('8/7/25', '8/1/25'))
    text = inputs['monthly_text'].replace('2025-08-07', '2025-08-01')
    p = inputs['transaction_report']; p.write_text(p.read_text().replace('Aug 7,', 'Aug 1,'))
    with pytest.raises(ValueError, match='independent opening snapshot'):
        preview(inputs, monthly_text=text)


def test_wrong_opening_account_blocks(inputs):
    p = inputs['opening_transaction_report']; p.write_text(p.read_text().replace('1234567', '7654321'))
    with pytest.raises(ValueError, match='same account'):
        preview(inputs)


def test_wrong_opening_month_blocks(inputs):
    p = inputs['opening_transaction_report']; p.write_text(p.read_text().replace('Jun 30,', 'Jun 29,'))
    with pytest.raises(ValueError, match='preceding standard month'):
        preview(inputs)


def test_unanchored_partial_opening_position_blocks(inputs):
    p = inputs['opening_transaction_report']; p.write_text(p.read_text().replace('-50000', '-40000', 1))
    with pytest.raises(ValueError, match='new-from-flat anchor'):
        preview(inputs)


def test_monthly_effect_must_match_opening_replay(inputs):
    with pytest.raises(ValueError, match='money controls did not pass|different execution effect'):
        preview(inputs, monthly_text=inputs['monthly_text'].replace('LIMIT B CLOSE', 'LIMIT B OPEN'))


def test_closing_inventory_disagreement_blocks(inputs):
    text = inputs['monthly_text'].replace('Open Position(s) at End of Month',
        'Open Position(s) at End of Month\nEUR/USD - TOTAL POSITION: Sell (1) $0.00 $0.00')
    with pytest.raises(ValueError, match='closing Forex quantities differ'):
        preview(inputs, monthly_text=text)


def test_six_decimal_history_rate_mismatch_is_not_rounding(inputs):
    p = inputs['trade_history']; p.write_text(p.read_text().replace('@1.166891', '@1.166892'))
    with pytest.raises(ValueError, match='money controls did not pass'):
        preview(inputs)


def test_large_monthly_money_disagreement_blocks(inputs):
    with pytest.raises(ValueError, match='money controls did not pass'):
        preview(inputs, monthly_text=inputs['monthly_text'].replace('Settled Profit/Loss   ($9.99)', 'Settled Profit/Loss   ($9.00)'))


@pytest.mark.parametrize('flag', [False, None, 1])
def test_explicit_development_flag_required(inputs, flag):
    with pytest.raises(ValueError, match='development-only'):
        preview(inputs, development_only=flag)


@pytest.mark.parametrize('key', ['RENDER', 'RENDER_SERVICE_ID'])
def test_render_is_prohibited(inputs, monkeypatch, key):
    monkeypatch.setenv(key, 'true')
    with pytest.raises(ValueError, match='outside Render'):
        preview(inputs)


def test_stale_opening_evidence_is_rejected(inputs, monkeypatch):
    real = module._first_daily_positions
    def changed(*args):
        result = real(*args)
        p = inputs['opening_transaction_report']; p.write_text(p.read_text() + '\n')
        return result
    monkeypatch.setattr(module, '_first_daily_positions', changed)
    with pytest.raises(ValueError, match='Source evidence changed'):
        preview(inputs)


def test_duplicate_evidence_paths_are_not_accepted(inputs):
    with pytest.raises(ValueError, match='distinct CSV'):
        preview(inputs, opening_transaction_report=inputs['transaction_report'])


def tied_inputs(inputs):
    old = '2025-08-07 07:02:50 EUR/USD LIMIT B CLOSE 50,000 1.166891 12345 $3.00'
    new = '2025-08-07 07:02:50 EUR/USD LIMIT S OPEN (100,000) 1.166892 54321 $6.00'
    text = inputs['monthly_text'].replace(old, old + '\n' + new)
    text = text.replace('Open Position(s) at End of Month',
        'Open Position(s) at End of Month\nEUR/USD - TOTAL POSITION: Sell (100,000) $0.00 $0.00')
    p = inputs['trade_history']
    p.write_text(p.read_text().rstrip() + '\n,8/7/25,05:02:50,TRD,="54321","SOLD -100,000 EUR/USD @1.166892",--,--,--,991\n\n')
    output = io.StringIO(); w = csv.writer(output)
    w.writerow(['="54321"', 'Aug 7, 2025 09:02:50', 'Aug 7, 2025 17:00:00', 'new',
        'EUR/USD', 'Sell', '1.166892', '-100000', '116689.20', '0.00', '1.166892', '', '-100000'])
    p = inputs['transaction_report']; p.write_text(p.read_text() + output.getvalue())
    return text, old, new


def test_equal_time_executions_require_same_explicit_order_in_both_sources(inputs):
    text, _, _ = tied_inputs(inputs)
    result = preview(inputs, monthly_text=text)
    assert [r['position_effect'] for r in result['execution_candidates']] == ['CLOSE', 'OPEN']
    assert all(r['ordering_basis'] == 'history_and_monthly_detail_order' for r in result['execution_candidates'])
    assert result['closing_quantities'] == {'EUR/USD': '-100000'}


def test_disagreed_equal_time_execution_order_is_a_blocker(inputs):
    text, old, new = tied_inputs(inputs)
    with pytest.raises(ValueError, match='Equal-time execution order differs'):
        preview(inputs, monthly_text=text.replace(old + '\n' + new, new + '\n' + old))
