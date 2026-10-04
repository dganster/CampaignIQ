"""Recover only transactions corroborated by independent broker evidence."""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.position_event import PositionEvent
from campaigniq.domain.option_contract import OptionContract
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


def next_business_day(value):
    value += timedelta(days=1)
    while value.weekday() >= 5:
        value += timedelta(days=1)
    return value


def recover_statement_allocations(filename, statement_lines, trades, *, start, end):
    """Corroborate special zero-fill order records with an actual statement purchase.

    An order, an ending snapshot or a quantity difference alone never creates
    a trade. Ambiguous candidates and partially represented purchases fail closed.
    """
    statement = ThinkorswimSourceReader().read(str(filename))
    try:
        section = statement.section("Account Order History")
    except KeyError:
        return ()
    header = section.header()
    candidates = []
    for raw in section.rows():
        row = dict(zip(header, raw))
        if (row.get("Status") != "(0) FILLED" or row.get("Type") != "STOCK"
                or row.get("Side") != "BUY" or row.get("Pos Effect") != "TO OPEN"):
            continue
        try:
            placed = datetime.strptime(row["Time Placed"], "%m/%d/%y %H:%M:%S")
            quantity = Decimal(row["Qty"].replace(",", ""))
            price = Decimal(row["PRICE"].replace(",", ""))
        except (ValueError, KeyError, InvalidOperation):
            continue
        if start <= placed.date() <= end and quantity > 0 and price > 0:
            candidates.append((row["Symbol"], placed.date(), quantity, price))
    if not candidates:
        return ()
    purchases = []
    transaction_date = None
    for line in statement_lines:
        dated = re.match(r"\s*(\d{2})/(\d{2})(?:\s|$)", line)
        if dated:
            transaction_date = date(start.year, int(dated[1]), int(dated[2]))
        purchase = re.match(r"\s*(?:\d{2}/\d{2}\s+)?Purchase\s+([A-Z][A-Z0-9./-]*)\s+(.+?)\s{2,}(\d[\d,]*\.\d+)\s+(\d[\d,]*\.\d+)\s+\(([\d,]+\.\d+)\)\s*$", line)
        if purchase and transaction_date:
            symbol, description, quantity, price, amount = purchase.groups()
            if re.search(r"\b(CALL|PUT|EXP)\b", description):
                continue
            q, p, a = (Decimal(v.replace(",", "")) for v in (quantity, price, amount))
            if abs(q * p - a) <= Decimal("0.01"):
                purchases.append((symbol, transaction_date, q, p))
    recovered = []
    for candidate in candidates:
        symbol, economic_date, quantity, price = candidate
        if candidates.count(candidate) != 1:
            continue
        matches = [p for p in purchases if p == (symbol, next_business_day(economic_date), quantity, price)]
        if len(matches) != 1:
            continue
        # Any existing execution for this symbol/day requires manual comparison;
        # do not infer a remaining fill from a partially represented trade.
        if any(leg.instrument == Instrument(symbol) and any(e.executed_at.date() == economic_date for e in leg.executions)
               for trade in trades for leg in trade.legs):
            continue
        recovered.append(Trade((Leg(
            instrument=Instrument(symbol), side=Side.BUY,
            position_effect=PositionEffect.OPEN,
            executions=(Execution(quantity, price, datetime.combine(economic_date, datetime.min.time())),),
        ),)))
    return tuple(recovered)


def carry_assignment_deliveries(assignments, previous_deliveries, *, period_start):
    """Close the option without repeating a delivery accepted in the prior month."""
    unused = list(previous_deliveries)
    adjusted = []
    for assignment in assignments:
        changes = list(assignment.changes)
        for delivery in list(unused):
            if (delivery.occurred_at.date() >= period_start
                    or next_business_day(delivery.occurred_at.date()) != assignment.occurred_at.date()
                    or assignment.occurred_at.date() != next_business_day(period_start - timedelta(days=1))):
                continue
            equity, = delivery.changes
            if isinstance(equity.instrument, OptionContract):
                continue
            if equity in changes:
                changes.remove(equity)
                unused.remove(delivery)
                break
        adjusted.append(PositionEvent(assignment.kind, tuple(changes), assignment.occurred_at))
    return tuple(adjusted)
