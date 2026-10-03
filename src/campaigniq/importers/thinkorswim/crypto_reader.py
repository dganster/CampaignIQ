"""Read crypto evidence separately from brokerage and FOREX positions."""

from __future__ import annotations

import csv
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


ZERO = Decimal("0")
TRADE = re.compile(
    r"^(BOT|SOLD)\s+([+-]?[\d.]+)\s+([A-Z0-9]+/[A-Z]+)\s+@([\d,.]+)$"
)


def money(text: str) -> Decimal:
    value = text.strip().replace("$", "").replace(",", "")
    if value in {"", "--"}:
        return ZERO
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Unrecognized crypto amount.") from exc
    if not result.is_finite():
        raise ValueError("Non-finite crypto amount.")
    return result


def _date(text: str) -> date:
    return datetime.strptime(text, "%m/%d/%y").date()


def _key(fill: dict) -> tuple:
    return (fill["executed_at"], fill["pair"], fill["side"],
            Decimal(fill["quantity"]), Decimal(fill["price_usd"]))


def read_crypto_report(
    path: str | Path, *, period_start: date, period_end: date,
) -> dict | None:
    """Retain all selected-month fills and statement rows, without deduplication.

    Account Trade History execution dates define fill dates. Crypto statement
    trade dates and execution dates are stored separately; references are not
    unique fill IDs because partial fills share the same reference.
    """
    statement = ThinkorswimSourceReader().read(str(path))
    try:
        history = statement.section("Account Trade History")
    except KeyError:
        return None
    fills = []
    current_timestamp = None
    for index, row in enumerate(csv.reader(history.lines)):
        if len(row) < 11 or row[9].strip().upper() != "CRYPTO":
            if len(row) > 1 and row[1].strip():
                current_timestamp = row[1].strip()
            continue
        timestamp = row[1].strip() or current_timestamp
        if not timestamp:
            raise ValueError("Crypto execution is missing its timestamp.")
        current_timestamp = timestamp
        executed = datetime.strptime(timestamp, "%m/%d/%y %H:%M:%S")
        if not period_start <= executed.date() <= period_end:
            continue
        side, pair = row[3].strip().upper(), row[6].strip().upper()
        quantity, price = money(row[4]), money(row[10])
        if side not in {"BUY", "SELL"} or not pair.endswith("/USD"):
            raise ValueError("Crypto currently supports USD-quoted buys and sells only.")
        if quantity == ZERO or price <= ZERO:
            raise ValueError("Crypto quantity and price must be nonzero and positive respectively.")
        if (side == "BUY" and quantity < ZERO) or (side == "SELL" and quantity > ZERO):
            raise ValueError("Crypto quantity sign does not match its side.")
        fills.append({
            "fill_id": f"history-row-{index}", "executed_at": executed.isoformat(),
            "pair": pair, "side": side, "quantity": format(abs(quantity), "f"),
            "price_usd": str(price), "position_effect": row[5].strip(),
            "reference": None, "principal_usd": None, "fee_usd": None,
            "trade_date": None,
        })

    crypto_sections = [s for s in statement.sections if s.name.startswith('"Crypto #')]
    if len({s.name.split(" (")[0] for s in crypto_sections}) > 1:
        raise ValueError("Multiple crypto accounts in one export are not yet supported.")
    ledgers = [s for s in crypto_sections if s.name.rstrip('"').endswith("Statements")]
    snapshots = [s for s in crypto_sections if s not in ledgers]
    if not fills and not crypto_sections:
        return None

    warnings = []
    ledger_rows, ledger_fills = [], []
    previous_balance = None
    control_deltas = []
    opening_cash = None
    cash_end = None
    funding = ZERO
    for section in ledgers:
        for index, row in enumerate(csv.reader(section.lines)):
            if len(row) < 9 or row[0] == "Trade Date":
                continue
            try:
                exec_date = _date(row[1])
            except ValueError:
                if any(cell.strip() for cell in row):
                    raise ValueError("Unrecognized crypto cash-ledger row.")
                continue
            if exec_date > period_end:
                continue
            amount, fee, balance = money(row[7]), money(row[6]), money(row[8])
            kind = row[3].strip()
            if kind == "BAL":
                delta = balance - previous_balance if previous_balance is not None else ZERO
                previous_balance = balance
                if exec_date < period_start:
                    opening_cash = balance
                elif exec_date == period_start and opening_cash is None:
                    opening_cash = balance
            else:
                delta = (balance - (previous_balance + amount + fee)
                         if previous_balance is not None else None)
                previous_balance = balance
            if exec_date < period_start:
                continue
            cash_end = balance
            if delta is None or delta != ZERO:
                control_deltas.append(None if delta is None else str(delta))
            event = {
                "ledger_id": f"crypto-row-{index}", "trade_date": _date(row[0]).isoformat(),
                "executed_at": datetime.combine(exec_date,
                    datetime.strptime(row[2], "%H:%M:%S").time()).isoformat(),
                "type": kind, "reference": row[4].removeprefix('="').removesuffix('"'),
                "description": row[5], "amount_usd": str(amount),
                "fee_cash_change_usd": str(fee), "balance_usd": str(balance),
            }
            ledger_rows.append(event)
            if kind == "FND":
                funding += amount
            if kind == "TRD":
                match = TRADE.fullmatch(row[5].strip())
                if match is None:
                    raise ValueError("Unrecognized crypto trade in the cash ledger.")
                side = "BUY" if match[1] == "BOT" else "SELL"
                if (side == "BUY" and amount > ZERO) or (side == "SELL" and amount < ZERO):
                    raise ValueError("Crypto principal cash change does not match trade side.")
                if fee > ZERO:
                    raise ValueError("Unexpected crypto fee credit; review the source.")
                ledger_fills.append({
                    "executed_at": event["executed_at"], "pair": match[3], "side": side,
                    "quantity": str(abs(money(match[2]))), "price_usd": str(money(match[4])),
                    "reference": event["reference"], "principal_usd": str(abs(amount)),
                    "fee_usd": str(-fee), "trade_date": event["trade_date"],
                })
            elif kind not in {"BAL", "FND"}:
                warnings.append(f"Crypto cash activity {kind} retained for review.")

    ledger_match = Counter(map(_key, fills)) == Counter(map(_key, ledger_fills))
    if not ledger_match:
        warnings.append("Crypto execution history and cash-ledger fills do not match; amounts and fees need review.")
    pools = {}
    for fill in ledger_fills:
        pools.setdefault(_key(fill), []).append(fill)
    for fill in fills:
        candidates = pools.get(_key(fill), [])
        if candidates:
            matched = candidates.pop(0)
            for name in ("reference", "principal_usd", "fee_usd", "trade_date"):
                fill[name] = matched[name]

    holdings = {}
    for section in snapshots:
        for row in csv.reader(section.lines):
            if not row or row[0] == "Symbol" or not row[0]:
                continue
            if len(row) < 7 or not row[0].endswith("/USD"):
                raise ValueError("Unrecognized crypto holdings row.")
            if row[0] in holdings:
                raise ValueError("Duplicate crypto holdings row.")
            quantity = money(row[1])
            holdings[row[0]] = {
                "quantity": str(quantity), "quantity_precision": str(Decimal(1).scaleb(quantity.as_tuple().exponent)),
                "reported_trade_price_usd": str(money(row[2])),
                "reported_total_cost_usd": str(money(row[3])),
                "unrealized_pl_usd": str(money(row[4])), "net_liq_usd": str(money(row[6])),
            }
    if control_deltas or opening_cash is None or not ledger_rows:
        warnings.append("Crypto cash running-balance control is incomplete or mismatched.")
    warnings.append("Crypto holdings are an undated export snapshot, not an independently verified month-end statement.")
    heading = Path(path).read_text(encoding="utf-8-sig").splitlines()[0]
    coverage = re.search(r"since (\d{1,2}/\d{1,2}/\d{2}) through (\d{1,2}/\d{1,2}/\d{2})", heading)
    export_start = _date(coverage[1]).isoformat() if coverage else None
    export_end = _date(coverage[2]).isoformat() if coverage else None
    if export_start is None or _date(coverage[1]) > period_start or _date(coverage[2]) < period_end:
        warnings.append("The export heading does not confirm coverage of the entire selected month.")
    return {
        "format": "campaigniq.crypto_month", "version": 1,
        "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
        "export_start": export_start, "export_end": export_end,
        "account": crypto_sections[0].name.split(" (")[0].strip('"') if crypto_sections else None,
        "fills": fills, "cash_ledger": ledger_rows, "holdings_snapshot": holdings,
        "opening_cash_usd": str(opening_cash) if opening_cash is not None else None,
        "last_reported_cash_usd": str(cash_end) if cash_end is not None else None,
        "net_funding_usd": str(funding), "cash_control_reconciled": not control_deltas and opening_cash is not None and bool(ledger_rows),
        "cash_control_deltas_usd": control_deltas, "fill_control_reconciled": ledger_match,
        "warnings": warnings,
    }
