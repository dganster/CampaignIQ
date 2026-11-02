"""Strict layout-text reader for the supplied Schwab USD futures statement family.

Memo P/L rows are retained as non-cash evidence, never duplicate executions.
No account identifier is returned or guessed from the statement heading.
"""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import re
from datetime import date

from campaigniq.futures_source_audit import money

MONTH_CODES = dict(zip(('JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'), 'FGHJKMNQUVXZ'))


def _series(description):
    description = ' '.join(description.split())
    match = re.fullmatch(r'(CALL|PUT) ([A-Z]{3}) (\d{2}) (.*?) ([\d.]+)', description)
    if not match or match[2] not in MONTH_CODES:
        raise ValueError('Unsupported futures PDF option description')
    product = match[4]
    if product == 'IMM EOM M S&P':
        root = 'EW'
    elif re.fullmatch(r'CME EMINWEDW[1-5]', product):
        root = 'E' + product[-1] + 'C'
    elif re.fullmatch(r'CME EMINMONW[1-5]', product):
        root = 'E' + product[-1] + 'A'
    elif re.fullmatch(r'CME MINIS&PW[1-4]', product):
        root = 'EW' + product[-1]
    else:
        raise ValueError('Unsupported futures PDF product; no generic S&P mapping')
    return '/' + root + MONTH_CODES[match[2]] + match[3], money(match[5]), match[1]


def read_activity(text):
    layout = None
    result = []
    for line in text.splitlines():
        if 'LONG/BUY' in line and 'SHRT/SELL' in line and 'PRICE/LEGND' in line:
            layout = (line.index('LONG/BUY')+len('LONG/BUY'),
                      line.index('SHRT/SELL')+len('SHRT/SELL'),
                      line.index('CC'), line.index('CREDIT')-4)
            continue
        if not re.match(r'^\d{2}/\d{2}/\d{2}(?:\s|$)', line):
            continue
        if layout is None:
            raise ValueError('Dated futures PDF row has no column heading')
        long_end, short_end, currency, boundary = layout
        executed = datetime.strptime(line[:8], '%m/%d/%y').date()
        if line[8:11].strip() != 'US' or line[currency-3:currency+3].strip() != 'US':
            raise ValueError('Unsupported futures statement currency or column layout')
        buy, sell = money(line[11:long_end]), money(line[long_end:short_end])
        debit, credit = money(line[currency+3:boundary]), money(line[boundary:])
        if any(n < 0 for n in (buy, sell, debit, credit)) or (debit and credit):
            raise ValueError('Invalid futures statement debit/credit columns')
        body = line[short_end:currency-3].strip()
        if body == 'Futures Cash Sweep':
            kind, description = 'SWEEP', body
        else:
            match = re.fullmatch(r'(.*?)\s+E\s+(NET PREM|MEMO P&L|P&L|FEE/COMM|EXER/ASSN)', body)
            if not match:
                raise ValueError('Unrecognized dated futures PDF activity')
            description, kind = match.groups()
            description = ' '.join(description.split())
        result.append(dict(date=executed, buy=buy, sell=sell,
                           cash=credit-debit, kind=kind, description=description))
    if not result:
        raise ValueError('No futures statement activity rows')
    return result


def verify_activity(text, fills, deliveries, *, year, month, premium_cash, all_fees, sweeps, futures_pnl, cash_activity, offsets):
    rows = read_activity(text)
    if any((r['date'].year, r['date'].month) != (year, month) for r in rows):
        raise ValueError('Futures PDF activity falls outside selected month')
    expected = defaultdict(lambda: [Decimal(0), Decimal(0)])
    actual = defaultdict(lambda: [Decimal(0), Decimal(0)])
    expected_exercises, actual_exercises = defaultdict(Decimal), defaultdict(Decimal)
    for fill in fills:
        key=(fill.executed_at.date(), fill.contract.series, fill.contract.strike,
             fill.contract.kind, 'BUY' if fill.quantity > 0 else 'SELL')
        expected[key][0] += abs(fill.quantity)
        expected[key][1] += fill.cash
    for event in deliveries:
        candidates = {fill.contract.series for fill in fills if fill.contract.future == event.future
                      and fill.contract.expiration == event.expiration and fill.contract.strike == event.strike
                      and fill.contract.kind == event.kind}
        if len(candidates) != 1:
            raise ValueError('Ambiguous option series for PDF exercise comparison')
        key=(event.executed_at.date(), candidates.pop(), event.strike, event.kind,
             'BUY' if event.option_quantity > 0 else 'SELL')
        expected_exercises[key] += abs(event.option_quantity)
    expected_offsets, actual_offsets = defaultdict(Decimal), defaultdict(Decimal)
    for event in offsets:
        if money(event['futures_offset_quantity']):
            expected_offsets[(date.fromisoformat(event['date']), event['future'])] += money(event['futures_offset_quantity'])
    total_premium, pdf_sweeps, pdf_pnl, exercise_fees, delivery_fees = (Decimal(0) for _ in range(5))
    for row in rows:
        kind, cash = row['kind'], row['cash']
        if kind == 'NET PREM':
            if bool(row['buy']) == bool(row['sell']):
                raise ValueError('Ambiguous PDF option transaction side')
            series, strike, option_kind = _series(row['description'])
            key=(row['date'], series, strike, option_kind, 'BUY' if row['buy'] else 'SELL')
            actual[key][0] += row['buy'] + row['sell']
            actual[key][1] += cash
            total_premium += cash
        elif kind == 'EXER/ASSN':
            if bool(row['buy']) == bool(row['sell']) or cash > 0:
                raise ValueError('Ambiguous PDF option exercise side or cash')
            series, strike, option_kind = _series(row['description'])
            key=(row['date'], series, strike, option_kind, 'BUY' if row['buy'] else 'SELL')
            actual_exercises[key] += row['buy'] + row['sell']
            exercise_fees += cash
        elif kind == 'MEMO P&L':
            if cash:
                raise ValueError('Unexpected monetary value on memo P/L row')
        elif kind in ('P&L', 'FEE/COMM'):
            # Narrow support: explicitly identified ES futures, never generic futures.
            future_match = re.fullmatch(r'(MAR|JUN|SEP|DEC) (\d{2}) EMINI S&P 500', row['description'])
            if not future_match:
                raise ValueError('Unsupported underlying futures PDF description')
            if row['buy'] != row['sell'] or not row['buy']:
                raise ValueError('Futures PDF offset quantities are ambiguous')
            future = '/ES' + MONTH_CODES[future_match[1]] + future_match[2]
            if kind == 'P&L':
                actual_offsets[(row['date'], future)] += row['buy']
                pdf_pnl += cash
            else:
                if (row['date'], future) not in expected_offsets or row['buy'] != expected_offsets[(row['date'], future)]:
                    raise ValueError('PDF futures fee row has conflicting contract or quantity')
                if cash > 0:
                    raise ValueError('Unexpected PDF futures fee credit')
                delivery_fees += cash
        elif kind == 'SWEEP':
            if row['buy'] or row['sell']:
                raise ValueError('Cash sweep has unexpected contract quantity')
            pdf_sweeps += cash
    if set(expected) != set(actual) or any(expected[k][0] != actual[k][0] for k in expected):
        raise ValueError('PDF option execution quantities do not match source fills')
    if any(actual[k][1] > expected[k][1] for k in expected):
        raise ValueError('PDF net premium exceeds corresponding gross premium')
    source_exercise_fees = sum((event.fee_cash for event in deliveries), Decimal(0))
    if (total_premium-premium_cash != all_fees-source_exercise_fees or
            exercise_fees+delivery_fees != source_exercise_fees):
        raise ValueError('PDF fee allocation and source fees differ')
    if dict(expected_exercises) != dict(actual_exercises):
        raise ValueError('PDF exercise quantities or strikes differ from source')
    if dict(expected_offsets) != dict(actual_offsets):
        raise ValueError('PDF underlying futures offset quantities or contract month differ')
    if pdf_sweeps != sweeps or pdf_pnl != futures_pnl:
        raise ValueError('PDF cash sweeps or futures P/L differ from source accounting')
    if sum((row['cash'] for row in rows), Decimal(0)) != cash_activity:
        raise ValueError('PDF individual cash activity does not equal statement summary')
    return dict(option_quantities=True, exercise_quantities=True, premium_and_fee_allocation=True,
                futures_pnl=True, underlying_offset_quantities=True, cash_sweeps=True, individual_cash_rows=True)
