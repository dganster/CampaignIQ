"""A position journal captured from the import's resolved opening lots and history.

This is presentation evidence. It never replaces authoritative lot state or
broker realized economics. A close is attributed to the lots actually consumed,
not to the campaign number of the closing order.
"""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import json

from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.persistence.lot_book_store import _serialize_instrument, _deserialize_instrument

FORMAT = "campaigniq.position_journal"


def _quantity(book, instrument, campaign_id):
    return sum((lot.quantity for lot in book.lots(instrument)
                if lot.campaign_id == campaign_id), Decimal("0"))


def capture_position_journal(result, period_start):
    """Capture signed changes and campaign balances from the exact import replay."""
    book = result.opening_lot_book.clone()
    entries = []
    campaign_ids = {id(trade): campaign.campaign_id
                    for campaign in result.campaigns for trade in campaign.trades}

    def entry(at, instrument, action, change, price, cid, lots, *, fills=0, opened_at=None):
        entries.append(dict(occurred_at=at.isoformat(), instrument=_serialize_instrument(instrument),
                            action=action, quantity_change=str(change),
                            price=None if price is None else str(price), campaign_id=cid,
                            position_after=str(_quantity(book, instrument, cid)),
                            lots=[dict(lot_id=lid, quantity=str(qty)) for lid, qty in lots],
                            fills=fills, opened_at=None if opened_at is None else opened_at.isoformat()))

    for instrument in book.instruments():
        groups = defaultdict(list)
        for lot in book.lots(instrument):
            groups[lot.campaign_id].append(lot)
        for cid, lots in groups.items():
            entry(datetime.combine(period_start, datetime.min.time()), instrument,
                  "Carried position", sum((lot.quantity for lot in lots), Decimal("0")), None, cid,
                  [(lot.lot_id, abs(lot.quantity)) for lot in lots],
                  opened_at=lots[0].opened_at if len(lots) == 1 else None)
            entries[-1]["opening_refs"] = [dict(lot_id=lot.lot_id, quantity=str(lot.quantity),
                                               opened_at=lot.opened_at.isoformat()) for lot in lots]

    for item in result.position_history.items():
        if item.trade is not None:
            trade = item.trade
            rolling = {leg.position_effect for leg in trade.legs} == {PositionEffect.OPEN, PositionEffect.CLOSE}
            for leg in trade.legs:
                instrument = leg.instrument
                before_ids = {lot.lot_id for lot in book.lots(instrument)}
                allocations = book.apply_trade(Trade((leg,)), campaign_id=campaign_ids.get(id(trade)))
                quantity = sum((abs(e.quantity) for e in leg.executions), Decimal("0"))
                if not quantity:
                    continue
                price = sum((abs(e.quantity) * e.execution_price for e in leg.executions), Decimal("0")) / quantity
                at = min(e.executed_at for e in leg.executions)
                sign = Decimal("1") if leg.side is Side.BUY else Decimal("-1")
                if leg.position_effect is PositionEffect.OPEN:
                    lots = [(lot.lot_id, abs(lot.quantity)) for lot in book.lots(instrument)
                            if lot.lot_id not in before_ids]
                    entry(at, instrument, "Roll: open" if rolling else "Open / add", sign * quantity,
                          price, campaign_ids.get(id(trade)), lots, fills=len(leg.executions))
                else:
                    groups = defaultdict(list)
                    for allocation in allocations:
                        groups[allocation.campaign_id].append((allocation.lot_id, allocation.quantity))
                    for cid, lots in groups.items():
                        entry(at, instrument, "Roll: close" if rolling else "Close / reduce",
                              sign * sum((q for _, q in lots), Decimal("0")), price, cid, lots,
                              fills=len(leg.executions))
        else:
            event = item.event
            before = {change.instrument: tuple(book.lots(change.instrument)) for change in event.changes}
            LotBookPeriodApplier()._apply_event(book, event)
            for instrument in before:
                old = {lot.lot_id: lot for lot in before[instrument]}
                new = {lot.lot_id: lot for lot in book.lots(instrument)}
                groups = defaultdict(list)
                for lid in old.keys() | new.keys():
                    prior, following = old.get(lid), new.get(lid)
                    delta = (following.quantity if following else Decimal("0")) - (prior.quantity if prior else Decimal("0"))
                    if delta:
                        cid = (following or prior).campaign_id
                        groups[cid].append((lid, delta))
                for cid, changes in groups.items():
                    entry(event.occurred_at, instrument, event.kind.value.title(),
                          sum((q for _, q in changes), Decimal("0")), None, cid,
                          [(lid, abs(q)) for lid, q in changes])
    # Replay must remain identical to the economic pipeline, including events.
    if lot_signature(book) != lot_signature(result.ending_lot_book):
        raise ValueError("Position journal replay differs from the imported ending lots.")
    return entries


def lot_signature(book):
    return sorted((repr(lot.instrument), lot.lot_id, str(lot.quantity), lot.opened_at.isoformat(), lot.campaign_id or "")
                  for instrument in book.instruments() for lot in book.lots(instrument))


def serialize_position_journal(result, *, period_start, period_end):
    return json.dumps(dict(format=FORMAT, version=1, period_start=period_start.isoformat(),
                           period_end=period_end.isoformat(), entries=capture_position_journal(result, period_start)),
                      sort_keys=True, indent=2) + "\n"


def deserialize_position_journal(text, *, period_start, period_end):
    data = json.loads(text)
    if (data.get("format") != FORMAT or data.get("version") != 1
            or data.get("period_start") != period_start.isoformat()
            or data.get("period_end") != period_end.isoformat()):
        raise ValueError("Position journal format or reporting period is invalid.")
    for row in data["entries"]:
        required = {"occurred_at", "instrument", "action", "quantity_change", "price", "campaign_id", "position_after", "lots", "fills", "opened_at"}
        if not isinstance(row, dict) or not required <= row.keys():
            raise ValueError("Position journal entry is incomplete.")
        if not isinstance(row["action"], str) or not row["action"]:
            raise ValueError("Position journal action is invalid.")
        if row["campaign_id"] is not None and not isinstance(row["campaign_id"], str):
            raise ValueError("Position journal campaign is invalid.")
        if not isinstance(row["fills"], int) or row["fills"] < 0 or not isinstance(row["lots"], list):
            raise ValueError("Position journal execution or lot evidence is invalid.")
        for lot in row["lots"]:
            if not isinstance(lot["lot_id"], str) or not lot["lot_id"] or Decimal(lot["quantity"]) < 0:
                raise ValueError("Position journal lot evidence is invalid.")
        for field in ("quantity_change", "position_after", "price"):
            if row[field] is not None and not Decimal(row[field]).is_finite():
                raise ValueError("Position journal quantity or price is invalid.")
        day = datetime.fromisoformat(row["occurred_at"]).date()
        if not period_start <= day <= period_end:
            raise ValueError("Position journal entry falls outside its reporting period.")
        _deserialize_instrument(row["instrument"])
        Decimal(row["quantity_change"])
        Decimal(row["position_after"])
    return data["entries"]
