"""Isolated ES futures-option accounting preview, never a publication authority.

Only explicitly supported ES quarterly underliers and European weekly/EOM series
are accepted. Raw series codes, source-row IDs and exercise rows remain distinct.
Opening/ending account-state and identity verification belong to the account layer.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
from pathlib import Path
import re

from campaigniq.futures_source_audit import audit, money
from campaigniq.futures_statement_activity import verify_activity
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader

ZERO = Decimal(0)
ES_MULTIPLIER = Decimal(50)
MONTHS = dict(zip('FGHJKMNQUVXZ', range(1, 13)))


@dataclass(frozen=True, order=True)
class ESFuture:
    symbol: str

    def __post_init__(self):
        if not re.fullmatch(r'/ES[HMUZ]\d{2}', self.symbol):
            raise ValueError('Unsupported futures contract; only quarterly ES is implemented')

    @property
    def multiplier(self):
        return ES_MULTIPLIER


@dataclass(frozen=True, order=True)
class ESOption:
    future: ESFuture
    series: str
    expiration: date
    strike: Decimal
    kind: str

    def __post_init__(self):
        match = re.fullmatch(r'/(E[1-5][ABCD]|EW[1-4]?)([FGHJKMNQUVXZ])(\d{2})', self.series)
        if (not match or self.kind not in ('CALL', 'PUT') or
                not self.strike.is_finite() or self.strike <= 0):
            raise ValueError('Unsupported ES option series or invalid contract')
        if MONTHS[match[2]] != self.expiration.month or int(match[3]) != self.expiration.year % 100:
            raise ValueError('Option series month/year conflicts with descriptive expiration')
        # This validates source consistency, not exchange expiry-calendar correctness.
        if (2000 + int(self.future.symbol[-2:]), MONTHS[self.future.symbol[-3]]) < (self.expiration.year, self.expiration.month):
            raise ValueError('Underlying futures month precedes option expiration')

    def label(self):
        return f'{self.future.symbol} {self.series} {self.expiration.isoformat()} {self.strike} {self.kind}'


@dataclass(frozen=True)
class OptionFill:
    row_id: str
    order_id: str
    executed_at: datetime
    contract: ESOption
    quantity: Decimal
    effect: str
    price: Decimal

    def __post_init__(self):
        if (not self.quantity.is_finite() or not self.price.is_finite() or
                self.quantity == 0 or self.quantity != self.quantity.to_integral_value() or
                self.price < 0 or self.effect not in ('TO OPEN', 'TO CLOSE')):
            raise ValueError('Invalid futures option fill')
        cash = -self.quantity * self.price * self.contract.future.multiplier
        if cash != cash.quantize(Decimal('.01')):
            raise ValueError('Futures leg cash has unsupported sub-cent precision')

    @property
    def cash(self):
        return -self.quantity * self.price * self.contract.future.multiplier


@dataclass(frozen=True)
class Delivery:
    row_id: str
    cash_row_id: str
    executed_at: datetime
    future: ESFuture
    expiration: date
    strike: Decimal
    kind: str
    future_quantity: Decimal
    reported_cash: Decimal
    fee_cash: Decimal

    def __post_init__(self):
        if (not self.future_quantity.is_finite() or self.future_quantity == 0 or
                self.future_quantity != self.future_quantity.to_integral_value() or
                not self.strike.is_finite() or self.strike <= 0 or
                self.kind not in ('CALL', 'PUT') or not self.reported_cash.is_finite() or
                not self.fee_cash.is_finite() or self.fee_cash > 0):
            raise ValueError('Invalid futures delivery')

    @property
    def option_quantity(self):
        return self.future_quantity if self.kind == 'CALL' else -self.future_quantity


class LotBook:
    """FIFO point-value accounting. Closes never silently flip or invent lots."""
    def __init__(self):
        self.lots = defaultdict(deque)
        self.realized = ZERO

    @staticmethod
    def validate(contract, quantity, price):
        if (not isinstance(contract, (ESFuture, ESOption)) or not quantity.is_finite() or
                quantity == 0 or quantity != quantity.to_integral_value() or
                not price.is_finite() or price < 0):
            raise ValueError('Invalid futures lot operation')

    def open(self, contract, quantity, price, row_id):
        self.validate(contract, quantity, price)
        lots = self.lots[contract]
        if lots and (lots[0][0] > 0) != (quantity > 0):
            raise ValueError('Opening trade conflicts with existing position direction')
        lots.append([quantity, price, row_id])

    def close(self, contract, quantity, price):
        self.validate(contract, quantity, price)
        lots = self.lots[contract]
        available = sum((lot[0] for lot in lots), ZERO)
        if not available or (available > 0) == (quantity > 0) or abs(quantity) > abs(available):
            raise ValueError('Closing trade lacks sufficient verified opening lots')
        remaining = abs(quantity)
        realized = ZERO
        while remaining:
            lot = lots[0]
            amount = min(remaining, abs(lot[0]))
            signed = amount if lot[0] > 0 else -amount
            multiplier = contract.future.multiplier if isinstance(contract, ESOption) else contract.multiplier
            realized += signed * (price - lot[1]) * multiplier
            lot[0] -= signed
            remaining -= amount
            if not lot[0]:
                lots.popleft()
        self.realized += realized
        return realized

    def deliver(self, contract, quantity, price, row_id):
        self.validate(contract, quantity, price)
        lots = self.lots[contract]
        available = sum((lot[0] for lot in lots), ZERO)
        realized = ZERO
        if available and (available > 0) != (quantity > 0):
            offset = min(abs(available), abs(quantity)) * (1 if quantity > 0 else -1)
            realized = self.close(contract, offset, price)
            quantity -= offset
        if quantity:
            self.open(contract, quantity, price, row_id)
        return realized

    def positions(self):
        return {contract: sum((lot[0] for lot in lots), ZERO)
                for contract, lots in self.lots.items() if lots}


def read_fills(source, year, month):
    section = source.section('Account Trade History')
    c = section.columns()
    fills = []
    execution = None
    group = None
    group_is_future = None
    for index, row in enumerate(section.rows()):
        stamp = row[c['Exec Time']].strip()
        if stamp:
            execution = datetime.strptime(stamp, '%m/%d/%y %H:%M:%S')
            group = f'history-order-{index}'
            group_is_future = None
        symbol, series = row[c['Symbol']].strip(), row[c['Exp']].strip()
        is_future = symbol.startswith('/') or series.startswith('/')
        if execution is None:
            if is_future:
                raise ValueError('Futures continuation leg has no order header')
            continue
        if group_is_future is not None and is_future != group_is_future:
            raise ValueError('Mixed futures and securities legs in one source order')
        group_is_future = is_future
        if not is_future or (execution.year, execution.month) != (year, month):
            continue
        match = re.fullmatch(r'(/ES[HMUZ]\d{2}) 1/50 (\d{1,2} [A-Z]{3} \d{2})(?: \([^\n]*\))*', symbol)
        if not match:
            raise ValueError('Unsupported futures-option description or multiplier')
        contract = ESOption(ESFuture(match[1]), series,
            datetime.strptime(match[2], '%d %b %y').date(), money(row[c['Strike']]), row[c['Type']])
        quantity, price = money(row[c['Qty']]), money(row[c['Price']])
        side, effect = row[c['Side']], row[c['Pos Effect']]
        if (side not in ('BUY', 'SELL') or effect not in ('TO OPEN', 'TO CLOSE') or
                quantity == 0 or quantity != quantity.to_integral_value() or
                (quantity > 0) != (side == 'BUY') or price < 0 or execution.date() > contract.expiration):
            raise ValueError('Invalid or post-expiration futures-option fill')
        fills.append(OptionFill(f'history-row-{index}', group, execution, contract, quantity, effect, price))
    if any(a.executed_at > b.executed_at for a, b in zip(fills, fills[1:])):
        raise ValueError('Futures execution history is not chronological')
    return fills


EXERCISE = re.compile(r'Exercise, future settle ([+-]?[\d.]+) of (/ES[HMUZ]\d{2}) XCME 50 \(WEEKLY\) (\d{1,2} [A-Za-z]{3} \d{4}) ([\d.]+) (CALL|PUT)')


def read_ledger(source, year, month):
    section = source.section('Futures Statements')
    c = section.columns()
    rows = []
    for index, row in enumerate(section.rows()):
        executed = datetime.strptime(row[c['Exec Date']]+' '+row[c['Exec Time']], '%m/%d/%y %H:%M:%S')
        if (executed.year, executed.month) == (year, month):
            rows.append((index, executed, row))
    orders = []
    deliveries = []
    fees, sweeps, premiums = ZERO, ZERO, ZERO
    seen = set()
    for position, (index, executed, row) in enumerate(rows):
        kind, description = row[c['Type']], row[c['Description']]
        fee = money(row[c['Misc Fees']]) + money(row[c['Commissions & Fees']])
        amount = money(row[c['Amount']])
        if fee > 0:
            raise ValueError('Unexpected futures fee credit')
        fees += fee
        if kind == 'BAL':
            continue
        if kind == 'FSWP':
            if (description not in ('Cash sweep from equities account', 'Cash sweep to equities account') or
                    (description == 'Cash sweep from equities account' and amount <= 0) or
                    (description == 'Cash sweep to equities account' and amount >= 0)):
                raise ValueError('Unknown or incorrectly signed futures cash sweep')
            sweeps += amount
            continue
        if description == 'Opening futures position due to futures option expiration':
            if index not in seen:
                raise ValueError('Orphan futures delivery cash row')
            continue
        match = EXERCISE.fullmatch(description)
        if match:
            if amount:
                raise ValueError('Unexpected direct cash on exercise source row')
            if position+1 >= len(rows):
                raise ValueError('Missing paired futures delivery row')
            next_index, next_time, next_row = rows[position+1]
            if (next_row[c['Type']] != 'TRD' or next_time != executed or
                    next_row[c['Description']] != 'Opening futures position due to futures option expiration' or
                    money(next_row[c['Misc Fees']]) or money(next_row[c['Commissions & Fees']])):
                raise ValueError('Ambiguous futures delivery pairing')
            quantity = money(match[1])
            expiration = datetime.strptime(match[3], '%d %b %Y').date()
            if quantity == 0 or quantity != quantity.to_integral_value() or expiration != executed.date():
                raise ValueError('Invalid exercise quantity or exercise date')
            deliveries.append(Delivery(f'ledger-row-{index}', f'ledger-row-{next_index}', executed,
                ESFuture(match[2]), expiration, money(match[4]), match[5], quantity,
                money(next_row[c['Amount']]), fee))
            seen.add(next_index)
            continue
        series = re.search(r' (/(?:E[1-5][ABCD]|EW[1-4]?)[FGHJKMNQUVXZ]\d{2}):XCME ', description)
        if kind != 'TRD' or series is None or not description.startswith(('BOT ', 'SOLD ')):
            raise ValueError('Unrecognized futures activity; no cash rows skipped')
        orders.append((executed, series[1], amount))
        premiums += amount
    return orders, deliveries, premiums, fees, sweeps


def replay(fills, deliveries):
    identifiers = [f.row_id for f in fills] + [d.row_id for d in deliveries] + [d.cash_row_id for d in deliveries]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('Duplicate futures source row identity')
    options, futures = LotBook(), LotBook()
    events = [(f.executed_at, 0, i, f) for i, f in enumerate(fills)]
    events += [(d.executed_at, 1, i, d) for i, d in enumerate(deliveries)]
    output = []
    for _, _, _, event in sorted(events, key=lambda item: item[:3]):
        if isinstance(event, OptionFill):
            if event.effect == 'TO OPEN':
                options.open(event.contract, event.quantity, event.price, event.row_id)
            else:
                options.close(event.contract, event.quantity, event.price)
            continue
        matches = [contract for contract, qty in options.positions().items()
                   if contract.future == event.future and contract.expiration == event.expiration
                   and contract.strike == event.strike and contract.kind == event.kind
                   and (qty > 0) == (event.option_quantity > 0)]
        if len(matches) != 1:
            raise ValueError('Exercise has no unique matching option position')
        option = matches[0]
        options.close(option, -event.option_quantity, ZERO)
        prior = futures.positions().get(event.future, ZERO)
        offset = min(abs(prior), abs(event.future_quantity)) if prior and (prior > 0) != (event.future_quantity > 0) else ZERO
        realized = futures.deliver(event.future, event.future_quantity, event.strike, event.row_id)
        if realized != event.reported_cash:
            raise ValueError('Calculated exercise futures P/L differs from broker delivery cash')
        output.append(dict(source=event.row_id, cash_source=event.cash_row_id,
            date=event.executed_at.date().isoformat(), option=option.label(),
            future=event.future.symbol, futures_offset_quantity=str(offset),
            option_quantity_removed=str(event.option_quantity), futures_quantity=str(event.future_quantity),
            futures_entry_price=str(event.strike), realized_futures_usd=str(realized)))
    return options, futures, output


def preview(history: Path, statement_text: str, *, year: int, month: int) -> dict:
    before = hashlib.sha256(history.read_bytes()).hexdigest()
    controls = audit(history, statement_text, year=year, month=month)
    if not controls['cash_reconciled']:
        raise ValueError('Independent futures statement cash controls failed')
    source = ThinkorswimSourceReader().read(str(history))
    fills = read_fills(source, year, month)
    orders, deliveries, premium_cash, fees, sweeps = read_ledger(source, year, month)
    grouped = defaultdict(list)
    for fill in fills:
        grouped[fill.order_id].append(fill)
    expected = []
    for group in grouped.values():
        if len({fill.contract.series for fill in group}) != 1:
            raise ValueError('Cross-series futures orders require separate matching support')
        expected.append((group[0].executed_at, group[0].contract.series, sum((f.cash for f in group), ZERO)))
    if Counter(expected) != Counter(orders):
        raise ValueError('Every futures order must match broker premium cash without deduplication')
    options, futures, exercises = replay(fills, deliveries)
    activity_checks = verify_activity(statement_text, fills, deliveries, year=year, month=month,
        premium_cash=premium_cash, all_fees=fees, sweeps=sweeps, futures_pnl=futures.realized, offsets=exercises,
        cash_activity=money(controls['statement_controls']['cash_activity']))
    gross = options.realized + futures.realized
    net = gross + fees
    remaining = bool(options.positions() or futures.positions())
    # With open positions premium cash is not realized P/L. Never label it as such.
    if not remaining and gross != premium_cash + sum((d.reported_cash for d in deliveries), ZERO):
        raise ValueError('Flat-book realized result disagrees with broker trading cash')
    if not remaining and net != money(controls['statement_controls']['cash_activity']) - sweeps:
        raise ValueError('Realized result and independent statement net activity differ')
    if hashlib.sha256(history.read_bytes()).hexdigest() != before:
        raise ValueError('Source changed while preview was being computed')
    return dict(format='campaigniq.futures_accounting_preview', version=1,
        publication_authorized=False, production_integration_enabled=False,
        opening_state_assumption='Empty opening ES book; not independently verified',
        opening_positions_verified=False, ending_positions_independently_verified=False,
        source_sha256=before, period=f'{year:04d}-{month:02d}', multiplier_usd='50',
        option_execution_legs=len(fills), matched_option_orders=len(expected),
        option_premium_cash_usd=str(premium_cash), option_realized_usd=str(options.realized),
        futures_realized_usd=str(futures.realized), fees_cash_usd=str(fees),
        realized_net_usd=str(net), cash_sweeps_usd=str(sweeps),
        calculated_ending_options={c.label(): str(q) for c,q in sorted(options.positions().items())},
        calculated_ending_futures={c.symbol: str(q) for c,q in sorted(futures.positions().items())},
        exercises=exercises, statement_cash_checks=controls['checks'],
        statement_activity_checks=activity_checks,
        blockers=['Independent account identity and opening/ending position evidence are not verified by this preview.',
                  'Separate monthly account storage and publication integration remain pending.',
                  'Crypto remains separate and requires its own statement.'])


def main():
    """Local source-only command. It cannot save, deploy or publish a month."""
    import argparse
    import json
    import subprocess
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trade-history', type=Path, required=True)
    parser.add_argument('--futures-statement', type=Path, required=True)
    parser.add_argument('--year', type=int, required=True)
    parser.add_argument('--month', type=int, required=True)
    parser.add_argument('--development-only', action='store_true', required=True)
    args = parser.parse_args()
    try:
        date(args.year, args.month, 1)
        pdf = args.futures_statement.read_bytes()
        if not pdf.startswith(b'%PDF-'):
            raise ValueError('Expected a futures statement PDF')
        output = subprocess.run(['pdftotext', '-layout', str(args.futures_statement.resolve()), '-'],
                                capture_output=True, text=True, check=True, timeout=60)
        result = preview(args.trade_history, output.stdout, year=args.year, month=args.month)
        if args.futures_statement.read_bytes() != pdf:
            raise ValueError('Futures statement changed during preview')
        result['statement_sha256'] = hashlib.sha256(pdf).hexdigest()
    except (ValueError, OSError, KeyError, IndexError, ArithmeticError, subprocess.SubprocessError):
        print(json.dumps(dict(format='campaigniq.futures_accounting_preview', version=1,
            publication_authorized=False, production_integration_enabled=False,
            error='Futures accounting preview failed; no data was changed.')))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
