"""Read option position changes from Schwab Pending / Open Activity."""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.lot import InstrumentLike
from campaigniq.domain.value_objects.instrument import Instrument

ACTION=re.compile(r"\b(?P<action>Cover Short|Short Sale)\s+(?P<symbol>[A-Z][A-Z0-9.]*)\s+(?P<type>CALL|PUT)\b.*?(?P<qty>[\d,]+\.\d{4})\b")
EXP=re.compile(r"(?P<x>\d{2}/\d{2}/\d{4})")
STRIKE=re.compile(r"(?P<x>[\d,]+(?:\.\d+)?)\s+[CP]\b")

@dataclass(frozen=True, slots=True)
class SchwabPendingOptionActivity:
    instrument: OptionContract
    quantity_change: Decimal
    activity_date: date | None
    settlement_date: date | None = None

def read_pending_option_activity(lines: list[str]) -> tuple[SchwabPendingOptionActivity,...]:
    out=[]; inside=False; current_date=None
    for i,line in enumerate(lines):
        text=line.strip()
        if text.startswith("Pending / Open Activity"):
            inside=True; continue
        if inside and text.startswith("Endnotes For Your Account"):
            break
        if not inside: continue
        m=ACTION.search(line)
        if not m: continue
        em=sm=None
        for detail in lines[i+1:i+4]:
            em=em or EXP.search(detail)
            sm=sm or STRIKE.search(detail)
        if not em or not sm:
            raise ValueError(f"Missing pending option details for {m.group('symbol')}")
        expiration=datetime.strptime(em.group("x"),"%m/%d/%Y").date()
        # The first MM/DD before the action is the Activity Date. Schwab
        # carries it across continuation rows. The MM/DD after the action is
        # the Settle/Payable Date and is preserved separately.
        prefix = line[: line.find(m.group("action"))]
        dm=re.search(r"\b(\d{2})/(\d{2})\b",prefix)
        if dm:
            current_date=date(expiration.year,int(dm.group(1)),int(dm.group(2)))
        suffix = line[line.find(m.group("action")):]
        settlement_match = re.search(r"\b(\d{2})/(\d{2})\b", suffix)
        settlement_date = (
            date(
                expiration.year,
                int(settlement_match.group(1)),
                int(settlement_match.group(2)),
            )
            if settlement_match
            else None
        )
        qty=Decimal(m.group("qty").replace(",",""))
        change=qty if m.group("action")=="Cover Short" else -qty
        out.append(SchwabPendingOptionActivity(
            OptionContract(m.group("symbol"),expiration,Decimal(sm.group("x").replace(",","")),OptionType[m.group("type")]),
            change,current_date,settlement_date))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class SchwabPendingPositionActivity:
    instrument: InstrumentLike
    quantity_change: Decimal
    activity_date: date | None
    settlement_date: date | None = None


def _activity_date(month: int, day: int, period_end: date) -> date:
    candidates = []
    for year in (period_end.year - 1, period_end.year, period_end.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            pass
    return min(candidates, key=lambda value: abs((value - period_end).days))


def _settlement_date(month: int, day: int, activity: date) -> date:
    result = date(activity.year, month, day)
    if result < activity:
        if activity.month != 12 or month != 1:
            raise ValueError("Pending settlement date precedes activity date.")
        result = date(activity.year + 1, month, day)
    return result


def read_pending_position_activity(
    lines: list[str], *, period_end: date,
) -> tuple[SchwabPendingPositionActivity, ...]:
    """Read explicit pending options and stock purchases/sales in the statement.

    Dates on continuation rows inherit the preceding activity date. Settlement
    dates remain separate and are anchored across December/January boundaries.
    """
    out = []
    for row in read_pending_option_activity(lines):
        activity = (
            _activity_date(row.activity_date.month, row.activity_date.day, period_end)
            if row.activity_date is not None else None
        )
        settlement = (
            _settlement_date(row.settlement_date.month, row.settlement_date.day, activity)
            if row.settlement_date is not None and activity is not None else None
        )
        out.append(SchwabPendingPositionActivity(
            row.instrument, row.quantity_change, activity, settlement,
        ))

    equity = re.compile(
        r"\b(?P<action>Purchase|Sale)\s+(?P<symbol>[A-Z][A-Z0-9.]*)\s+"
        r"(?P<description>.*?)\s+(?P<qty>[\d,]+\.\d{4})\b"
    )
    inside = False
    current_date = None
    for line in lines:
        text = line.strip()
        if text.startswith("Pending / Open Activity"):
            inside = True
            current_date = None
            continue
        if text.startswith(("Total Pending Transactions", "Endnotes For Your Account")):
            inside = False
        if not inside:
            continue
        option_match = ACTION.search(line)
        match = equity.search(line)
        action_match = option_match or match
        if action_match is None:
            continue
        prefix = line[:action_match.start()]
        dated = re.search(r"\b(\d{2})/(\d{2})\b", prefix)
        if dated:
            current_date = _activity_date(int(dated[1]), int(dated[2]), period_end)
        if option_match is not None or match is None:
            continue
        if prefix.rstrip().endswith("Short") or re.search(r"\b(CALL|PUT)\b", match["description"]):
            continue
        settle = re.search(r"\b(\d{2})/(\d{2})\b", line[match.end():])
        settlement = (
            _settlement_date(int(settle[1]), int(settle[2]), current_date)
            if settle is not None and current_date is not None else None
        )
        quantity = Decimal(match["qty"].replace(",", ""))
        if quantity <= 0:
            raise ValueError("Pending stock quantity must be positive.")
        out.append(SchwabPendingPositionActivity(
            Instrument(match["symbol"]),
            quantity if match["action"] == "Purchase" else -quantity,
            current_date, settlement,
        ))
    return tuple(out)
