"""Read dated Schwab Crypto statements without combining partial fills."""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import re
from campaigniq.importers.thinkorswim.crypto_reader import money

NUMBER = r'(?:\([\d,.]+\)|[+-]?[\d,.]+|--)'


def read_crypto_statement(path, *, period_start, period_end):
    text = Path(path).read_text(encoding='utf-8')
    if not re.search(r'Schwab Crypto(?:TM|™)?\s+Account', text):
        raise ValueError('The optional Crypto Statement must be a Schwab Crypto account statement.')
    periods = set()
    for month, first, last, year in re.findall(r'([A-Z][a-z]+)\s+(\d{1,2})\s*-\s*(\d{1,2}),\s*(\d{4})', text):
        try:
            start = datetime.strptime(f'{month} {first} {year}', '%B %d %Y').date()
            end = start.replace(day=int(last))
        except ValueError:
            continue
        periods.add((start, end))
    if periods != {(period_start, period_end)}:
        raise ValueError('Crypto Statement period must exactly match the selected calendar month.')
    accounts = set(re.findall(r'\*{4}-\*+(\d{4})', text))
    if len(accounts) != 1:
        raise ValueError('Crypto Statement account identifier is missing or ambiguous.')
    cash = re.findall(r'^\s*CASH\s+('+NUMBER+r')\s+('+NUMBER+r')\s*$', text, re.M)
    if len(cash) != 1:
        raise ValueError('Crypto Statement cash balances are missing or ambiguous.')
    holdings = {}
    transactions = []
    section = None
    current_date = None
    for line in text.splitlines():
        if 'Position Details - Crypto' in line:
            section = 'positions'
        elif 'Transaction Details' in line:
            section = 'transactions'
        elif line.strip() == 'Disclosures':
            section = None
        if section == 'positions':
            match = re.match(r'^\s*([A-Z0-9]+/USD)\s+.+?\s+('+NUMBER+r')\s+('+NUMBER+r')\s+('+NUMBER+r')\s*$', line)
            if match:
                pair, quantity, price, value = match.groups()
                if pair in holdings:
                    raise ValueError('Duplicate Crypto Statement holding.')
                holdings[pair] = dict(quantity=str(money(quantity)), price_usd=str(money(price)), market_value_usd=str(money(value)),
                                      precision=str(Decimal(1).scaleb(money(quantity).as_tuple().exponent)))
        elif section == 'transactions':
            match = re.match(r'^\s*(?:(\d{2}/\d{2})\s+)?(BUY|SELL|DEPOSIT|WITHDRAWAL)\s+([A-Z0-9]+/USD|CASH)\s+.+?\s+('+NUMBER+r')\s+('+NUMBER+r')\s+('+NUMBER+r')\s+('+NUMBER+r')\s*$', line)
            if match:
                day, action, pair, quantity, price, fee, amount = match.groups()
                if day:
                    current_date = datetime.strptime(f'{day}/{period_start.year}', '%m/%d/%Y').date()
                if current_date is None or not period_start <= current_date <= period_end:
                    raise ValueError('Crypto Statement transaction date is outside the selected month or missing.')
                transactions.append(dict(date=current_date.isoformat(), action=action, pair=pair,
                    quantity=str(money(quantity)), price_usd=str(money(price)), fee_usd=str(abs(money(fee))), amount_usd=str(money(amount))))
            elif re.match(r'^\s*\d{2}/\d{2}\s+\S+', line) or re.search(r'\b(?:BUY|SELL|DEPOSIT|WITHDRAWAL)\s+(?:[A-Z0-9]+/USD|CASH)\b', line):
                raise ValueError('Unrecognized Crypto Statement transaction layout.')
    beginning, ending = (money(value) for value in cash[0])
    delta = beginning + sum((Decimal(t['amount_usd']) for t in transactions), Decimal(0)) - ending
    return dict(period_start=period_start.isoformat(), period_end=period_end.isoformat(), account_suffix=next(iter(accounts)),
                beginning_cash_usd=str(beginning), ending_cash_usd=str(ending), holdings=holdings, transactions=transactions,
                cash_rollforward_delta_usd=str(delta))


def attach_crypto_statement(report, statement):
    if report is None:
        raise ValueError('Crypto Statement supplied but no crypto evidence exists in Account Trade History.')
    result = deepcopy(report)
    suffix = re.search(r'(\d{4})$', report.get('account') or '')
    if suffix is None or suffix[1] != statement['account_suffix']:
        raise ValueError('Crypto Statement account does not match the Account Trade History crypto account.')
    def export_key(fill):
        if fill['fee_usd'] is None or fill['principal_usd'] is None:
            raise ValueError('Crypto Statement matching requires the cash-ledger fees and principal for every fill.')
        amount = (-Decimal(fill['principal_usd']) - Decimal(fill['fee_usd']) if fill['side']=='BUY'
                  else Decimal(fill['principal_usd']) - Decimal(fill['fee_usd']))
        return (fill['executed_at'][:10], fill['side'], fill['pair'], Decimal(fill['quantity']), Decimal(fill['price_usd']), Decimal(fill['fee_usd']), amount)
    expected = Counter(export_key(fill) for fill in report['fills'])
    observed = Counter((row['date'], row['action'], row['pair'], Decimal(row['quantity']), Decimal(row['price_usd']), Decimal(row['fee_usd']), Decimal(row['amount_usd']))
                       for row in statement['transactions'] if row['action'] in {'BUY','SELL'})
    if expected != observed:
        raise ValueError('Crypto Statement fills, prices, fees, or amounts do not match the export, including partial fills.')
    if report.get('last_reported_cash_usd') is None or Decimal(report['last_reported_cash_usd']) != Decimal(statement['ending_cash_usd']):
        raise ValueError('Crypto Statement ending cash does not match the export.')
    for pair in set(report['ending_quantities']) | set(statement['holdings']):
        holding = statement['holdings'].get(pair)
        expected_quantity = Decimal(report['ending_quantities'].get(pair,'0'))
        if holding is None:
            if expected_quantity != 0:
                raise ValueError('Crypto Statement is missing a computed ending holding.')
        elif abs(expected_quantity - Decimal(holding['quantity'])) > Decimal(holding['precision']) / 2:
            raise ValueError('Crypto Statement ending quantity does not match computed holdings.')
    result['crypto_statement'] = statement
    result['statement_fills_reconciled'] = True
    result['statement_ending_balances_reconciled'] = True
    result['statement_cash_rollforward_reconciled'] = Decimal(statement['cash_rollforward_delta_usd']) == 0
    result['month_end_reconciled'] = (result['statement_cash_rollforward_reconciled'] and report['cash_control_reconciled'] and report['fill_control_reconciled'])
    result['status'] = 'statement reconciled' if result['month_end_reconciled'] else 'statement ending balances confirmed; cash reconciliation incomplete'
    if not result['statement_cash_rollforward_reconciled']:
        result['warnings'].append('Crypto Statement beginning cash plus listed transactions differs from ending cash by $'+statement['cash_rollforward_delta_usd']+'. Statement ending balances and fills match, but the month is not fully reconciled.')
    return result
