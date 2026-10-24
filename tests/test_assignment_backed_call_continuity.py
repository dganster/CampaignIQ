"""Call openings inherit observed assignment provenance without merging old IDs."""
from dataclasses import replace
from datetime import datetime, date
from decimal import Decimal as D
import importlib.util
from pathlib import Path
import pytest

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.campaign_continuity import plan_campaign_continuity
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect as Effect
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind as Kind
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.persistence.lot_book_store import serialize_lot_book


def helpers():
    spec=importlib.util.spec_from_file_location('opening_helpers',Path(__file__).with_name('test_backfill_opening_provenance.py'))
    h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h);return h


def scenario(*,shares='500',qty='5',assignment=True,extra_owner=False,call_type=OptionType.CALL,
             side=Side.SELL,effect=Effect.OPEN,at=None,split=False,partial=False,existing=None):
    h=helpers();old='2025-01:ADBE:CAMP-000001';new='2025-02:ADBE:CAMP-000004'
    book=h.seed(h.snapshot('put',h.PUT,'-5',old))
    if not assignment:book=h.seed(h.snapshot('shares',h.STOCK,shares,old))
    if extra_owner:book.seed(h.snapshot('other-shares',h.STOCK,'500','DISTINCT'))
    call=replace(h.PUT,option_type=call_type)
    if existing is not None:
        book.seed(h.snapshot('existing-call',call,existing,'OTHER_CALL'))
    history=PositionHistory()
    if assignment:
        history.add_event(PositionEvent(kind=Kind.ASSIGNMENT,occurred_at=datetime(2025,1,8),
            changes=(PositionChange(h.PUT,D(5)),PositionChange(h.STOCK,D(shares)))))
    if split:
        history.add_event(PositionEvent(kind=Kind.SPLIT,occurred_at=datetime(2025,1,9),
            changes=(PositionChange(h.STOCK,D(shares)),)))
    quantities=['2','2','1'] if partial else [qty]
    trades=[]
    for index,q in enumerate(quantities):
        leg=h.leg(call,qty=q,side=side,effect=effect)
        if at is not None:leg=replace(leg,executions=tuple(replace(e,executed_at=at) for e in leg.executions))
        else:leg=replace(leg,executions=tuple(replace(e,executed_at=datetime(2025,1,22,9,11,index)) for e in leg.executions))
        trade=Trade((leg,));history.add_trade(trade);trades.append(trade)
    campaign=Campaign(new,tuple(trades));before=serialize_lot_book(period_end=date(2024,12,31),lot_book=book)
    def plan(flag=True):
        p=plan_campaign_continuity(opening_lot_book=book,position_history=history,campaigns=(campaign,),link_assignment_calls=flag)
        assert before==serialize_lot_book(period_end=date(2024,12,31),lot_book=book)
        assert campaign.campaign_id==new and campaign.trades==tuple(trades)
        return p
    return plan,old,new


def test_observed_put_delivery_then_call_open_keeps_existing_campaign():
    run,old,_=scenario();p=run()
    assert not p.blocking_findings and p.campaigns[0].campaign_id==old
    link=p.trade_bindings[0]
    assert link.supported_by_fifo and link.evidence_kind=='assignment_backed_covered_call'
    assert link.covered_equity_lot_ids and not link.consumed_lot_ids


def test_default_mode_does_not_apply_the_new_policy():
    run,_,new=scenario();p=run(False)
    assert p.campaigns[0].campaign_id==new and not p.trade_bindings[0].covered_equity_lot_ids


def test_split_fills_share_only_available_collateral():
    run,old,_=scenario(partial=True);p=run()
    assert not p.blocking_findings and p.campaigns[0].campaign_id==old
    assert len(p.trade_bindings)==3
    assert all(link.evidence_kind=='assignment_backed_covered_call' for link in p.trade_bindings)


@pytest.mark.parametrize('kwargs',[
    dict(shares='400'),dict(qty='6'),dict(qty='5.1'),dict(assignment=False),dict(extra_owner=True),
    dict(call_type=OptionType.PUT),dict(side=Side.BUY),dict(existing='-1'),dict(existing='1'),
    dict(at=datetime(2025,1,7)),dict(at=datetime(2025,1,8)),dict(split=True),
])
def test_missing_ambiguous_or_insufficient_coverage_is_not_inferred(kwargs):
    run,_,new=scenario(**kwargs);p=run()
    assert not any(link.covered_equity_lot_ids for link in p.trade_bindings)
    assert all(link.target_campaign_id==new for link in p.trade_bindings)


def test_close_leg_is_never_treated_as_a_covered_call_open():
    run,_,_=scenario(effect=Effect.CLOSE)
    p=run()
    assert p.blocking_findings and not p.trade_bindings[0].covered_equity_lot_ids


def test_policy_flag_is_not_truthy_coercion():
    run,_,_=scenario()
    with pytest.raises(ValueError,match='explicit boolean'):run(1)


def test_linked_call_and_assigned_equity_can_close_without_a_false_merge():
    h=helpers();call=replace(h.PUT,option_type=OptionType.CALL)
    old='OLD';book=h.seed(h.snapshot('put',h.PUT,'-5',old));history=PositionHistory()
    history.add_event(PositionEvent(kind=Kind.ASSIGNMENT,occurred_at=datetime(2025,1,8),
        changes=(PositionChange(h.PUT,D(5)),PositionChange(h.STOCK,D(500)))))
    opened=Trade((h.leg(call,qty='5'),))
    close_call=h.leg(call,qty='5',side=Side.BUY,effect=Effect.CLOSE)
    close_stock=h.leg(h.STOCK,qty='500',side=Side.SELL,effect=Effect.CLOSE)
    closed=Trade(tuple(replace(leg,executions=tuple(replace(e,executed_at=datetime(2025,1,23)) for e in leg.executions)) for leg in (close_call,close_stock)))
    history.add_trade(opened);history.add_trade(closed)
    p=plan_campaign_continuity(opening_lot_book=book,position_history=history,
        campaigns=(Campaign('NEW',(opened,closed)),),link_assignment_calls=True)
    assert not p.blocking_findings and p.campaigns[0].campaign_id==old
    assert all(link.supported_by_fifo for link in p.trade_bindings)
