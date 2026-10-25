"""Ancestry evidence must survive ambiguity, stale inputs and inventory checks."""
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
from campaigniq.historical_backfill_ancestry import replay_ancestry, _validate_history_header
from campaigniq.persistence.lot_book_store import serialize_lot_book, deserialize_lot_book

STOCK = Instrument('URI')
PUT = OptionContract('URI', date(2025, 2, 21), D(800), OptionType.PUT)
NEXT = replace(PUT, expiration=date(2025, 3, 21), strike=D(810))
CALL = replace(NEXT, strike=D(690), option_type=OptionType.CALL)
START, END = date(2025, 1, 1), date(2025, 6, 30)


def book(*positions):
    b = LotBook()
    for index, (instrument, quantity) in enumerate(positions):
        b.seed(Lot(f'snapshot-{index}', instrument, D(quantity), datetime(2024, 12, 31, 23, 59, 59),
                   D('1234.56'), basis_source='BROKER'))
    return b


def trade(instrument=PUT, quantity='-5', effect=PE.OPEN, day=1):
    side = Side.SELL if D(quantity) < 0 else Side.BUY
    execution = Execution(D(quantity), D(10), datetime(2025, 1, day, 10))
    leg = (OptionLeg(instrument, side, effect, (execution,), broker_strategy='SINGLE')
           if isinstance(instrument, OptionContract) else InstrumentLeg(instrument, side, effect, (execution,)))
    return Trade((leg,))


def roll(quantity='5', day=2, old=PUT, new=NEXT):
    # OPEN first matches the broker's actual roll leg ordering.
    return Trade(trade(new, '-' + quantity, day=day).legs +
                 trade(old, quantity, PE.CLOSE, day).legs)


def assignment(quantity='5', day=3, option=NEXT, shares='500'):
    return PositionEvent(EK.ASSIGNMENT, (PositionChange(option, D(quantity)),
        PositionChange(STOCK, D(shares))), datetime(2025, 1, day))


def audit(tmp_path, opening, closing, trades=(), events=(), **kwargs):
    path = tmp_path / 'broker-source.csv'
    if not path.exists():
        path.write_text('synthetic broker evidence\n')
    h = PositionHistory()
    for t in trades:
        h.add_trade(t)
    for e in events:
        h.add_event(e)
    args = dict(opening_lot_book=opening, closing_lot_book=closing, position_history=h,
        period_start=START, period_end=END, evidence_paths=(path,), development_only=True)
    args.update(kwargs)
    before = serialize_lot_book(period_end=END, lot_book=opening)
    try:
        return replay_ancestry(**args)
    finally:
        assert serialize_lot_book(period_end=END, lot_book=opening) == before


def test_boundary_roll_assignment_and_covered_call_share_one_diagnostic_anchor(tmp_path):
    opening = book((PUT, '-5'))
    closing = book((STOCK, '500'), (CALL, '-5'))
    before = serialize_lot_book(period_end=END, lot_book=closing)
    a = audit(tmp_path, opening, closing, [roll(), trade(CALL, day=4)], [assignment()])
    preview, bindings = a.preview_bindings(closing, development_only=True)
    assert len(bindings) == 2
    assert len({cid for _, cid in bindings}) == 1
    assert a.origins[0][1] == 'started_before_data'
    assert [t[1] for t in a.transitions] == ['option_roll', 'assignment', 'covered_call']
    assert not a.publication_authorized
    assert serialize_lot_book(period_end=END, lot_book=closing) == before
    for i in closing.instruments():
        assert replace(preview.lots(i)[0], campaign_id=None) == closing.lots(i)[0]


def test_flat_reentry_creates_distinct_observed_origin(tmp_path):
    a = audit(tmp_path, book((PUT, '-5')), book((NEXT, '-5')),
              [trade(PUT, '5', PE.CLOSE), trade(NEXT, day=2)])
    assert [o[1] for o in a.origins] == ['started_before_data', 'observed_open']
    assert a.origins[0][0] != a.origins[1][0]


def test_identical_split_fills_are_valid_when_same_contract_rolls_commute(tmp_path):
    a = audit(tmp_path, book((PUT, '-5')), book((NEXT, '-5')),
              [roll('1'), roll('1'), roll('1'), roll('2')])
    assert len(a.transitions) == 4 and len(a.origins) == 1


def test_observed_first_open_is_not_called_a_pre_data_campaign(tmp_path):
    a = audit(tmp_path, book(), book((PUT, '-5')), [trade()])
    assert a.origins[0][1] == 'observed_open'
    assert a.origins[0][2].startswith('2025-01-01')


@pytest.mark.parametrize('flag', [False, None, 1, 'true'])
def test_explicit_development_flag_required(tmp_path, flag):
    with pytest.raises(ValueError, match='development-only'):
        audit(tmp_path, book(), book((PUT, '-5')), [trade()], development_only=flag)


@pytest.mark.parametrize('env', ['RENDER', 'RENDER_SERVICE_ID'])
def test_render_execution_prohibited(tmp_path, monkeypatch, env):
    monkeypatch.setenv(env, 'true')
    with pytest.raises(ValueError, match='outside Render'):
        audit(tmp_path, book(), book((PUT, '-5')), [trade()])


def test_missing_roll_leg_cannot_pass_even_with_matching_symbol(tmp_path):
    with pytest.raises(ValueError, match='New exposure'):
        audit(tmp_path, book((PUT, '-5')), book((PUT, '-5'), (NEXT, '-5')), [trade(NEXT)])


def test_mismatching_closing_quantity_blocks(tmp_path):
    with pytest.raises(ValueError, match='reconcile'):
        audit(tmp_path, book((PUT, '-5')), book((NEXT, '-4')), [roll()])


def test_offsetting_snapshot_lots_cannot_hide_gross_exposure(tmp_path):
    with pytest.raises(ValueError, match='Offsetting'):
        audit(tmp_path, book(), book((PUT, '-6'), (PUT, '1')), [trade()])


def test_unknown_or_multiple_consumed_roots_block_roll(tmp_path):
    with pytest.raises(ValueError, match='multiple ancestry'):
        audit(tmp_path, book((PUT, '-2'), (PUT, '-3')), book((NEXT, '-5')), [roll()])


def test_assignment_cannot_create_phantom_option_inventory(tmp_path):
    with pytest.raises(ValueError, match='matching option inventory'):
        audit(tmp_path, book(), book((STOCK, '500')), events=[assignment()])


@pytest.mark.parametrize('changes', [
    (PositionChange(NEXT, D(5)), PositionChange(STOCK, D(400))),
    (PositionChange(STOCK, D(500)), PositionChange(NEXT, D(5))),
    (PositionChange(NEXT, D(5)), PositionChange(Instrument('OTHER'), D(500))),
])
def test_nonstandard_or_unordered_assignment_is_blocked(tmp_path, changes):
    with pytest.raises(ValueError):
        audit(tmp_path, book((NEXT, '-5')), book((STOCK, '500')),
              events=[PositionEvent(EK.ASSIGNMENT, changes, datetime(2025, 1, 3))])


def test_covered_call_cannot_exceed_existing_cover(tmp_path):
    with pytest.raises(ValueError, match='Insufficient uncommitted'):
        audit(tmp_path, book((STOCK, '500'), (CALL, '-2')), book((STOCK, '500'), (CALL, '-7')),
              [trade(CALL)])


def test_closing_more_than_available_never_uses_new_roll_opening(tmp_path):
    with pytest.raises(ValueError, match='Insufficient'):
        audit(tmp_path, book((PUT, '-4')), book((NEXT, '-5')), [roll()])


def test_existing_opening_identity_is_not_replaced(tmp_path):
    opening = book((PUT, '-5'))
    opening.assign_campaign('snapshot-0', 'CANONICAL')
    with pytest.raises(ValueError, match='known IDs'):
        audit(tmp_path, opening, book((NEXT, '-5')), [roll()])


def test_known_snapshot_identity_is_not_overwritten(tmp_path):
    a = audit(tmp_path, book(), book((PUT, '-5')), [trade()])
    snapshot = book((PUT, '-5'))
    snapshot.assign_campaign('snapshot-0', 'CANONICAL')
    with pytest.raises(ValueError, match='existing campaign'):
        a.preview_bindings(snapshot, development_only=True)


def test_preview_requires_explicit_development_flag(tmp_path):
    a = audit(tmp_path, book(), book((PUT, '-5')), [trade()])
    with pytest.raises(ValueError, match='development-only'):
        a.preview_bindings(book((PUT, '-5')))


def test_preview_rehashes_evidence_and_refuses_changes(tmp_path):
    a = audit(tmp_path, book(), book((PUT, '-5')), [trade()])
    (tmp_path / 'broker-source.csv').write_text('changed')
    with pytest.raises(ValueError, match='evidence changed'):
        a.preview_bindings(book((PUT, '-5')), development_only=True)


def test_preview_cannot_ignore_extra_contract_for_same_target(tmp_path):
    a = audit(tmp_path, book(), book((PUT, '-5')), [trade()])
    with pytest.raises(ValueError, match='Complete target'):
        a.preview_bindings(book((PUT, '-5'), (NEXT, '-1')), development_only=True)


def test_preview_preserves_unrelated_holdings(tmp_path):
    a = audit(tmp_path, book(), book((PUT, '-5')), [trade()])
    unrelated = Instrument('OTHER')
    original = book((PUT, '-5'), (unrelated, '100'))
    preview, _ = a.preview_bindings(original, development_only=True)
    assert preview.lots(unrelated) == original.lots(unrelated)


def test_equal_time_assignment_and_trade_is_ambiguous(tmp_path):
    t = trade(CALL, day=3)
    e = replace(assignment(), occurred_at=datetime(2025, 1, 3, 10))
    with pytest.raises(ValueError, match='Equal timestamps'):
        audit(tmp_path, book((NEXT, '-5')), book((STOCK, '500'), (CALL, '-5')), [t], [e])


def test_exact_same_history_object_cannot_be_consumed_twice(tmp_path):
    t = roll('1')
    with pytest.raises(ValueError, match='Duplicate'):
        audit(tmp_path, book((PUT, '-2')), book((NEXT, '-2')), [t, t])


def test_history_outside_requested_period_is_blocked(tmp_path):
    with pytest.raises(ValueError, match='outside the replay period'):
        audit(tmp_path, book(), book((PUT, '-5')), [trade()], period_start=date(2025, 2, 1))


def test_distinct_source_paths_required(tmp_path):
    p = tmp_path / 'source'; p.write_text('source')
    with pytest.raises(ValueError, match='Distinct'):
        audit(tmp_path, book(), book((PUT, '-5')), [trade()], evidence_paths=[p, p])


def test_in_scope_instrument_flat_at_end_cannot_be_silently_reintroduced(tmp_path):
    other = Instrument('OTHER')
    a = audit(tmp_path, book((PUT, '-5')), book((other, '100')),
        [trade(PUT, '5', PE.CLOSE), trade(other, '100', day=2)])
    with pytest.raises(ValueError, match='Complete target'):
        a.preview_bindings(book((other, '100'), (PUT, '-5')), development_only=True)


def test_cli_bad_manifest_returns_failure_without_publication_authority(tmp_path):
    manifest = tmp_path / 'manifest.json'; manifest.write_text('{}')
    r = subprocess.run([sys.executable, '-m', 'campaigniq.historical_backfill_ancestry',
        '--development-only', '--evidence-manifest', str(manifest)], capture_output=True, text=True)
    assert r.returncode == 1
    assert json.loads(r.stdout)['publication_authorized'] is False


def test_expiration_retains_origin_but_no_phantom_position(tmp_path):
    other = Instrument('OTHER')
    expiration = PositionEvent(EK.EXPIRATION, (PositionChange(PUT, D(5)),), datetime(2025, 1, 2))
    a = audit(tmp_path, book((PUT, '-5')), book((other, '100')),
        [trade(other, '100', day=3)], [expiration])
    assert a.transitions[0][1] == 'expiration'
    assert not deserialize_lot_book(a._ending).lot_book.lots(PUT)


@pytest.mark.parametrize('end', ['1/30/25', '1/31/25'])
def test_broker_header_day_convention_preserved(end):
    _validate_history_header(f'Account Statement for 12345678SCHW (Test) since 12/31/24 through {end}', '12345678', 1)


@pytest.mark.parametrize('account,start,end', [
    ('87654321', '12/31/24', '1/30/25'),
    ('12345678', '1/31/25', '2/27/25'),
    ('12345678', '12/31/24', '1/15/25'),
])
def test_wrong_account_or_swapped_month_header_rejected(account, start, end):
    with pytest.raises(ValueError):
        _validate_history_header(f'Account Statement for {account}SCHW (Test) since {start} through {end}', '12345678', 1)
