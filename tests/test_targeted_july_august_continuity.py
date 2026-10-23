"""Targeted continuity must reconcile each boundary and exact monthly replay."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal as D
import json
import subprocess
import sys

import pytest
from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_effect import PositionEffect as PE
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind as EK
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.lot_book_store import serialize_lot_book
import campaigniq.historical_backfill_targeted_continuity as module

URI = OptionContract('URI', date(2025, 2, 21), D(800), OptionType.PUT)
URI_JUNE = replace(URI, expiration=date(2025, 8, 15))
URI_JULY = replace(URI, expiration=date(2025, 9, 19))
URI_AUG = replace(URI, expiration=date(2025, 10, 17))
ISRG = replace(URI_JUNE, underlying='ISRG', strike=D(555))
CALL = replace(ISRG, expiration=date(2025, 9, 19), strike=D(530), option_type=OptionType.CALL)
LATER_CALL = replace(CALL, expiration=date(2025, 10, 17))
STOCK = Instrument('ISRG')


def book(*positions):
    result = LotBook()
    for n, (instrument, quantity) in enumerate(positions):
        result.seed(Lot(f'boundary-{n}', instrument, D(quantity), datetime(2024, 12, 31, 23, 59, 59), None))
    return result


def trade(i, quantity, month, day=2, effect=PE.OPEN):
    e = Execution(D(quantity), D(10), datetime(2025, month, day, 10))
    side = Side.SELL if e.quantity < 0 else Side.BUY
    leg = (OptionLeg(i, side, effect, (e,), 'SINGLE') if isinstance(i, OptionContract)
           else InstrumentLeg(i, side, effect, (e,)))
    return Trade((leg,))


def roll(old, new, month):
    return Trade(trade(new, '-5', month).legs + trade(old, '5', month, effect=PE.CLOSE).legs)


def assign(quantity, month, day):
    return PositionEvent(EK.ASSIGNMENT, (PositionChange(ISRG, D(quantity)),
        PositionChange(STOCK, D(quantity) * 100)), datetime(2025, month, day))


@pytest.fixture
def inputs(tmp_path):
    h = PositionHistory()
    for t in [trade(ISRG, '-5', 1, 1), roll(URI, URI_JUNE, 1), roll(URI_JUNE, URI_JULY, 7),
              trade(CALL, '-2', 7, 25), roll(URI_JULY, URI_AUG, 8),
              trade(CALL, '-3', 8, 4), trade(CALL, '5', 8, 8, PE.CLOSE),
              trade(LATER_CALL, '-5', 8, 9)]:
        h.add_trade(t)
    h.add_event(assign('2', 7, 24)); h.add_event(assign('3', 8, 1))
    p = tmp_path / 'evidence'; p.write_text('synthetic verified broker fixture')
    return dict(opening_lot_book=book((URI, '-5')), position_history=h,
        closing_lot_books={module.ENDS[0]: book((URI_JUNE, '-5'), (ISRG, '-5')),
            module.ENDS[1]: book((URI_JULY, '-5'), (ISRG, '-3'), (STOCK, '200'), (CALL, '-2')),
            module.ENDS[2]: book((URI_AUG, '-5'), (STOCK, '500'), (LATER_CALL, '-5'))},
        evidence_paths=[p], development_only=True)


def check(inputs, **overrides):
    args = dict(inputs, **overrides)
    originals = [inputs['opening_lot_book'], *inputs['closing_lot_books'].values()]
    before = [serialize_lot_book(period_end=module.ENDS[0], lot_book=b) for b in originals]
    try:
        return module.verify_targeted_continuity(**args)
    finally:
        assert before == [serialize_lot_book(period_end=module.ENDS[0], lot_book=b) for b in originals]


def test_independent_monthly_replay_matches_continuous_replay_and_all_snapshots(inputs):
    result = check(inputs)
    assert result['july_august_exact_replay_match'] is True
    assert [m['month'] for m in result['months']] == ['2025-06', '2025-07', '2025-08']
    assert all(m['target_inventory_reconciled'] for m in result['months'])
    assert len(result['state_input_sha256']) == 5
    assert len(result['origins']) == 2


def test_isrg_prior_two_and_new_three_contracts_keep_one_origin(inputs):
    result = check(inputs)
    for month in result['months']:
        ids = {cid for position in month['positions'] if 'ISRG' in position['instrument']
               for cid in position['diagnostic_ancestry_ids']}
        assert len(ids) == 1
        assert 'ISRG:2025-01-01' in next(iter(ids))


def test_targeted_success_does_not_authorize_other_checks(inputs):
    result = check(inputs)
    assert result['diagnostic_only'] is True
    for field in ['publication_authorized', 'forex_verified',
                  'full_portfolio_continuity_verified', 'existing_authoritative_campaigns_verified']:
        assert result[field] is False


@pytest.mark.parametrize('flag', [False, None, 1, 'true'])
def test_development_flag_must_be_explicit(inputs, flag):
    with pytest.raises(ValueError, match='development-only'):
        check(inputs, development_only=flag)


@pytest.mark.parametrize('key', ['RENDER', 'RENDER_SERVICE_ID'])
def test_render_execution_is_prohibited(inputs, monkeypatch, key):
    monkeypatch.setenv(key, 'true')
    with pytest.raises(ValueError, match='outside Render'):
        check(inputs)


@pytest.mark.parametrize('missing', module.ENDS)
def test_every_snapshot_is_required(inputs, missing):
    closings = dict(inputs['closing_lot_books']); del closings[missing]
    with pytest.raises(ValueError, match='Exactly June'):
        check(inputs, closing_lot_books=closings)


def test_extra_month_not_silently_ignored(inputs):
    closings = dict(inputs['closing_lot_books'], **{})
    closings[date(2025, 9, 30)] = book()
    with pytest.raises(ValueError, match='Exactly June'):
        check(inputs, closing_lot_books=closings)


@pytest.mark.parametrize('end', module.ENDS)
def test_each_broker_quantity_is_independently_checked(inputs, end):
    closings = dict(inputs['closing_lot_books'])
    original = closings[end]
    closings[end] = book(*[(i, '-4' if getattr(i, 'underlying', None) == 'URI'
                            else str(sum(l.quantity for l in original.lots(i))))
                           for i in original.instruments()])
    with pytest.raises(ValueError, match='reconcile'):
        check(inputs, closing_lot_books=closings)


def test_omitted_assignment_cannot_be_repaired_by_matching_symbol(inputs):
    h = PositionHistory()
    for item in inputs['position_history'].items():
        if item.event is None:
            h.add_trade(item.trade)
    with pytest.raises(ValueError):
        check(inputs, position_history=h)


def test_history_outside_final_boundary_is_not_silently_filtered(inputs):
    h = inputs['position_history']; h.add_trade(trade(CALL, '-1', 9))
    with pytest.raises(ValueError, match='January-August'):
        check(inputs)


def test_non_target_activity_blocks_instead_of_inflating_scope(inputs):
    inputs['position_history'].add_trade(trade(Instrument('OTHER'), '100', 8, 20))
    with pytest.raises(ValueError, match='outside the target scope'):
        check(inputs)


def test_non_target_snapshot_blocks(inputs):
    closings = dict(inputs['closing_lot_books'])
    closings[module.ENDS[1]] = book((Instrument('OTHER'), '100'))
    with pytest.raises(ValueError, match='Only ISRG and URI'):
        check(inputs, closing_lot_books=closings)


def test_failed_sequential_comparison_is_a_blocker(inputs, monkeypatch):
    monkeypatch.setattr(module, 'audit_replay', lambda **kw: {
        'supplied_states_match_replay': False, 'blocking_findings': ['wrong carried ID']})
    with pytest.raises(ValueError, match='Month-by-month replay differs'):
        check(inputs)


def test_late_source_change_blocks_even_after_successful_replays(inputs, monkeypatch):
    real = module.audit_replay
    def changed(**kwargs):
        result = real(**kwargs)
        inputs['evidence_paths'][0].write_text('changed during verification')
        return result
    monkeypatch.setattr(module, 'audit_replay', changed)
    with pytest.raises(ValueError, match='Evidence changed'):
        check(inputs)


def test_later_replay_cannot_change_an_earlier_identity(inputs, monkeypatch):
    real = module.replay_ancestry
    def changed(**kwargs):
        result = real(**kwargs)
        if kwargs['period_end'] == module.ENDS[1]:
            trace = list(result.transitions)
            trace[0] = (trace[0][0], trace[0][1], 'WRONG')
            result = replace(result, transitions=tuple(trace))
        return result
    monkeypatch.setattr(module, 'replay_ancestry', changed)
    with pytest.raises(ValueError, match='changes earlier ancestry'):
        check(inputs)


def test_cli_missing_files_reports_failure_without_publication_authority(tmp_path):
    r = subprocess.run([sys.executable, '-m', 'campaigniq.historical_backfill_targeted_continuity',
        '--development-only', '--evidence-dir', str(tmp_path)], text=True, capture_output=True)
    assert r.returncode == 1
    result = json.loads(r.stdout)
    assert result['publication_authorized'] is False
    assert 'Required evidence files are missing' in result['message']
