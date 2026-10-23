from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import json

import pytest

from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.historical_backfill_replay import ReplayPeriod, audit_replay
from campaigniq.persistence.lot_book_store import serialize_lot_book

JUNE = date(2025, 6, 30)
JULY = date(2025, 7, 31)


def text(book, end=JUNE):
    return serialize_lot_book(period_end=end, lot_book=book)


def book():
    result = LotBook()
    result.seed(Lot("HIST-META", Instrument("META"), Decimal("100"),
                    datetime(2025, 1, 2), Decimal("5000"), "BROKER", "campaign"))
    return result


def row(opening=None):
    opening = opening or book()
    history = PositionHistory()
    return ReplayPeriod(JULY, text(opening), text(opening, JULY), history, ())


def audit(rows, target=None, end=JULY):
    return audit_replay(workspace="workspace-dennis", target_period_end=JUNE,
                        target_closing_text=target or text(book()),
                        downstream_period_end=end, periods=rows)


def test_matching_states_do_not_authorize_publication_or_certify_evidence():
    result = audit([row()])
    assert result["supplied_states_match_replay"]
    assert not result["publication_authorized"]
    assert not result["evidence_completeness_verified"]
    assert len(result["state_input_sha256"]) == 3


@pytest.mark.parametrize("field,value", [
    ("quantity", "99"), ("quantity", "-100"), ("basis_total", "5001"),
    ("basis_source", "OTHER"), ("campaign_id", "other"), ("lot_id", "other"),
    ("opened_at", "2025-01-03T00:00:00"),
])
def test_equal_net_position_does_not_hide_lot_provenance_changes(field, value):
    original = row()
    payload = json.loads(original.opening_text)
    payload["lots"][0][field] = value
    result = audit([replace(original, opening_text=json.dumps(payload))])
    assert not result["supplied_states_match_replay"]
    assert "opening" in result["blocking_findings"][0]


def test_replays_activity_and_detects_bad_closing_without_mutating_inputs():
    opening = book()
    history = PositionHistory()
    history.add_event(PositionEvent(
        PositionEventKind.ASSIGNMENT,
        (PositionChange(Instrument("META"), Decimal("-25")),),
        datetime(2025, 7, 2)))
    before = text(opening)
    closing = LotBookPeriodApplier().apply(
        opening_lot_book=opening, position_history=history, campaigns=())
    good = ReplayPeriod(JULY, before, text(closing, JULY), history, ())
    assert audit([good])["supplied_states_match_replay"]
    assert text(opening) == before
    bad = replace(good, closing_text=text(opening, JULY))
    assert not audit([bad])["supplied_states_match_replay"]


@pytest.mark.parametrize("rows", [[], [row(), row()]])
def test_missing_or_duplicate_months_rejected(rows):
    with pytest.raises(ValueError, match="replay months"):
        audit(rows)


def test_no_skipping_july_through_december_to_reach_2026():
    january = replace(row(), period_end=date(2026, 1, 31))
    with pytest.raises(ValueError, match="replay months"):
        audit([january], end=date(2026, 1, 31))


def test_carry_chain_does_not_reset_to_incorrect_opening_state():
    july = row()
    august = ReplayPeriod(date(2025, 8, 31), text(LotBook(), JULY),
                          text(LotBook(), date(2025, 8, 31)), PositionHistory(), ())
    result = audit([july, august], end=date(2025, 8, 31))
    assert len(result["blocking_findings"]) == 2


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(next_id=True),
    lambda p: p["lots"].append(dict(p["lots"][0])),
    lambda p: p["lots"][0].update(quantity="NaN"),
    lambda p: p["lots"][0].update(basis_total="Infinity"),
    lambda p: p["lots"][0].update(quantity="0"),
    lambda p: p.update(period_end="2025-05-31"),
])
def test_invalid_state_fails_closed(mutation):
    payload = json.loads(text(book()))
    mutation(payload)
    with pytest.raises(ValueError):
        audit([row()], target=json.dumps(payload))


def test_out_of_period_event_rejected():
    history = PositionHistory()
    history.add_event(PositionEvent(
        PositionEventKind.ASSIGNMENT,
        (PositionChange(Instrument("META"), Decimal("-1")),),
        datetime(2025, 8, 1)))
    with pytest.raises(ValueError, match="out-of-period"):
        audit([replace(row(), position_history=history)])


def test_next_lot_id_changes_are_blocking():
    original = row()
    payload = json.loads(original.closing_text)
    payload["next_id"] = 99
    result = audit([replace(original, closing_text=json.dumps(payload))])
    assert not result["supplied_states_match_replay"]


def test_fifo_reordering_blocks_even_with_same_lots_and_net_quantity():
    opening = book()
    opening.seed(Lot("HIST-META-2", Instrument("META"), Decimal("100"),
                     datetime(2025, 2, 2), Decimal("6000"), "BROKER", "campaign2"))
    original = row(opening)
    payload = json.loads(original.opening_text)
    payload["lots"].reverse()
    result = audit([replace(original, opening_text=json.dumps(payload))],
                   target=text(opening))
    assert not result["supplied_states_match_replay"]


def test_duplicate_json_fields_rejected():
    target = text(book()).replace('"next_id": 1', '"next_id": 1, "next_id": 2')
    with pytest.raises(ValueError, match="duplicate JSON"):
        audit([row()], target=target)


@pytest.mark.parametrize("field,value", [
    ("underlying", "UNH"), ("expiration", "2025-09-19"),
    ("strike", "610"), ("option_type", "PUT"),
])
def test_covered_call_contract_identity_mismatch_blocks(field, value):
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    opening = book()
    opening.seed(Lot("META-CALL", OptionContract(
        "META", date(2025, 8, 15), Decimal("600"), OptionType.CALL),
        Decimal("-1"), datetime(2025, 6, 1), None, None, "covered-call"))
    original = row(opening)
    payload = json.loads(original.opening_text)
    option = next(lot for lot in payload["lots"] if lot["instrument"]["type"] == "option")
    option["instrument"][field] = value
    result = audit([replace(original, opening_text=json.dumps(payload))],
                   target=text(opening))
    assert not result["supplied_states_match_replay"]


def test_complete_chain_crosses_year_boundary_without_skipping_months():
    from campaigniq.historical_backfill_impact import month_end
    opening = book()
    previous = JUNE
    rows = []
    for year, month in [(2025, m) for m in range(7, 13)] + [(2026, 1)]:
        end = month_end(year, month)
        rows.append(ReplayPeriod(end, text(opening, previous), text(opening, end),
                                 PositionHistory(), ()))
        previous = end
    result = audit(rows, end=date(2026, 1, 31))
    assert result["months_checked"] == 7
    assert result["supplied_states_match_replay"]
    assert not result["publication_authorized"]
