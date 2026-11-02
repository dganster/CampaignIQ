"""Read-only evidence audit. This does not import futures or authorize publication.

Futures-option series codes are retained separately from their descriptive dates.
No contract multiplier, security lot, or realized campaign is inferred here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import subprocess

from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


def money(value: str) -> Decimal:
    value = value.strip().replace(',', '')
    if value in ('', '--'):
        return Decimal(0)
    if value.endswith('-'):
        value = '-' + value[:-1]
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError('Invalid futures monetary value') from error
    if not result.is_finite():
        raise ValueError('Non-finite futures monetary value')
    return result


def statement_controls(text: str) -> dict:
    if 'MONTHLY STATEMENT' not in text or '*SEGREGATED USD*' not in text:
        raise ValueError('Unrecognized USD futures monthly statement')
    dates = set(re.findall(r'STATEMENT DATE:\s*([A-Z]{3} \d{1,2}, \d{4})', text))
    if len(dates) != 1:
        raise ValueError('Missing or conflicting futures statement dates')
    result = {'statement_date': datetime.strptime(dates.pop(), '%b %d, %Y').date().isoformat()}
    for label, key in (
        ('BEGINNING BALANCE', 'opening_cash'), ('ENDING BALANCE', 'closing_cash'),
        ("THIS MONTH'S ACTIVITY", 'cash_activity'),
        ('NET FUTURES PROFIT OR LOSS', 'net_futures_profit_loss'),
        ('NET OPTION PREMIUM', 'net_option_premium'),
        ('TOTAL COMMISSION AND FEES', 'commission_and_fees'),
    ):
        matches = re.findall(r'^\s*' + re.escape(label) + r'\s+([\d,.-]+)\s+([\d,.-]+)\s*$', text, re.M)
        if len(matches) != 1:
            raise ValueError('Missing or ambiguous futures statement control: ' + label)
        native, converted = map(money, matches[0])
        if native != converted:
            raise ValueError('Multi-currency futures statement requires separate currency support')
        result[key] = str(native)
    if money(result['opening_cash']) + money(result['cash_activity']) != money(result['closing_cash']):
        raise ValueError('Futures statement cash controls do not balance')
    return result


def audit(history: Path, statement_text: str, *, year: int, month: int) -> dict:
    # Only local file reads. No database imports or storage adapters.
    controls = statement_controls(statement_text)
    stated = date.fromisoformat(controls['statement_date'])
    if (stated.year, stated.month) != (year, month):
        raise ValueError('Futures statement is for another month')
    source = ThinkorswimSourceReader().read(str(history))
    for name in ('Account Trade History', 'Futures Statements'):
        if sum(section.name == name for section in source.sections) != 1:
            raise ValueError('Missing or duplicate futures source section')
    section = source.section('Account Trade History')
    columns = section.columns()
    legs = []
    execution = None
    for row in section.rows():
        timestamp = row[columns['Exec Time']].strip()
        if timestamp:
            execution = datetime.strptime(timestamp, '%m/%d/%y %H:%M:%S')
        symbol = row[columns['Symbol']].strip()
        series = row[columns['Exp']].strip()
        if not (symbol.startswith('/') or series.startswith('/')):
            continue
        if execution is None:
            raise ValueError('Futures leg has no execution timestamp')
        if (execution.year, execution.month) != (year, month):
            continue
        match = re.fullmatch(r'(/[A-Z0-9]+) 1/\d+ (\d{1,2} [A-Z]{3} \d{2})(?: \([^\n]*\))*', symbol)
        if not match or not re.fullmatch(r'/[A-Z0-9]+', series):
            raise ValueError('Unrecognized futures-option description; no rows skipped')
        expiration = datetime.strptime(match[2], '%d %b %y').date()
        side, kind, effect = (row[columns[name]].strip() for name in ('Side', 'Type', 'Pos Effect'))
        qty, price, strike = (money(row[columns[name]]) for name in ('Qty', 'Price', 'Strike'))
        if (side not in ('BUY', 'SELL') or kind not in ('CALL', 'PUT') or
                effect not in ('TO OPEN', 'TO CLOSE') or qty == 0 or qty != qty.to_integral_value() or
                (qty > 0) != (side == 'BUY') or price < 0 or strike <= 0):
            raise ValueError('Invalid futures-option execution')
        legs.append(dict(execution=execution.isoformat(), underlying=match[1], series=series,
                         expiration=expiration.isoformat(), side=side, effect=effect,
                         quantity=str(qty), strike=str(strike), option_type=kind, price=str(price)))
    if not legs:
        raise ValueError('No futures-option executions found for selected month')
    section = source.section('Futures Statements')
    c = section.columns()
    balance = None
    opening = None
    movements = Decimal(0)
    fees = Decimal(0)
    sweeps = Decimal(0)
    counts = Counter()
    for row in section.rows():
        executed = datetime.strptime(row[c['Exec Date']], '%m/%d/%y').date()
        if (executed.year, executed.month) != (year, month):
            continue
        current = money(row[c['Balance']])
        kind = row[c['Type']]
        amount, misc, commission = (money(row[c[name]]) for name in ('Amount', 'Misc Fees', 'Commissions & Fees'))
        if kind not in ('BAL', 'TRD', 'FSWP'):
            raise ValueError('Unrecognized futures ledger row; no rows skipped')
        if balance is None:
            if kind != 'BAL':
                raise ValueError('Futures ledger lacks an opening balance checkpoint')
            opening = current
        if kind == 'BAL':
            if amount or misc or commission or (balance is not None and balance != current):
                raise ValueError('Futures daily balance checkpoint does not reconcile')
        else:
            change = amount + misc + commission
            if balance + change != current:
                raise ValueError('Futures running cash balance does not reconcile')
            movements += change
            fees += misc + commission
            if kind == 'FSWP':
                if misc or commission:
                    raise ValueError('Unexpected fee on futures cash sweep')
                sweeps += amount
        counts[kind] += 1
        balance = current
    if opening is None:
        raise ValueError('Futures ledger is missing')
    checks = {
        'opening_cash': opening == money(controls['opening_cash']),
        'closing_cash': balance == money(controls['closing_cash']),
        'cash_activity': movements == money(controls['cash_activity']),
        'total_fees': fees == money(controls['commission_and_fees']),
        # Statement net figures already include fees. Do not subtract them twice.
        'statement_net_and_sweeps': money(controls['net_futures_profit_loss']) +
            money(controls['net_option_premium']) + sweeps == movements,
    }
    return dict(format='campaigniq.futures_source_audit', version=1,
                diagnostic_only=True, publication_authorized=False,
                history_sha256=hashlib.sha256(history.read_bytes()).hexdigest(),
                statement_controls=controls, checks=checks, cash_reconciled=all(checks.values()),
                futures_option_legs=len(legs), series=sorted({r['series'] for r in legs}),
                ledger_rows=dict(sorted(counts.items())), cash_sweeps=str(sweeps),
                limitations=['No futures accounting or publication support is enabled by this audit.',
                            'Contract dates are descriptive source evidence, not independently verified expiry rules.',
                            'Account ownership and full-month source coverage are not established by this diagnostic.',
                            'Crypto requires its separate account statement and controls.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trade-history', type=Path, required=True)
    parser.add_argument('--futures-statement', type=Path, required=True)
    parser.add_argument('--year', type=int, required=True)
    parser.add_argument('--month', type=int, required=True)
    args = parser.parse_args()
    try:
        date(args.year, args.month, 1)
        pdf = args.futures_statement
        if not pdf.read_bytes().startswith(b'%PDF-'):
            raise ValueError('A PDF futures statement is required')
        extracted = subprocess.run(['pdftotext', '-layout', str(pdf.resolve()), '-'],
                                   capture_output=True, text=True, check=True, timeout=60)
        result = audit(args.trade_history, extracted.stdout, year=args.year, month=args.month)
        result['statement_sha256'] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    except (ValueError, OSError, KeyError, IndexError, subprocess.SubprocessError):
        print(json.dumps(dict(format='campaigniq.futures_source_audit', version=1,
                              diagnostic_only=True, publication_authorized=False,
                              error='Source audit failed; no data was changed.')))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['cash_reconciled'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
