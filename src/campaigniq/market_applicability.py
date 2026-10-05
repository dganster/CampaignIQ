"""Reject not-applicable declarations that would hide retained market evidence."""
from datetime import datetime
from decimal import Decimal
import csv
import re
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader
from campaigniq.importers.thinkorswim.crypto_reader import read_crypto_report, money
from campaigniq.persistence.crypto_month import load_preceding_crypto


def _crypto_has_values(report, *, current):
    if not report:
        return False
    if current and report.get('fills'):
        return True
    if current and any(row.get('type') != 'BAL' or any(Decimal(row.get(field, '0')) != 0 for field in ('amount_usd', 'fee_cash_change_usd', 'balance_usd')) for row in report.get('cash_ledger', [])):
        return True
    fields = ('opening_cash_usd', 'last_reported_cash_usd', 'net_funding_usd') if current else ('last_reported_cash_usd',)
    for field in fields:
        value = report.get(field)
        if value is not None and Decimal(value) != 0:
            return True
    for value in report.get('holdings_snapshot', {}).values():
        if any(Decimal(value.get(field, '0')) != 0 for field in ('quantity', 'net_liq_usd')):
            return True
    for lots in report.get('ending_lots', {}).values():
        if any(Decimal(lot['quantity']) != 0 for lot in lots):
            return True
    return False


def validate_market_applicability(path, *, period_start, period_end, opening_lot_book,
                                  storage, forex_applicable=True, crypto_applicable=True):
    if forex_applicable and crypto_applicable:
        return
    if path is None:
        raise ValueError('Account Trade History is required to verify Not applicable choices.')
    if not crypto_applicable:
        current = read_crypto_report(path, period_start=period_start, period_end=period_end)
        previous = load_preceding_crypto(storage, period_start)
        if _crypto_has_values(current, current=True) or _crypto_has_values(previous, current=False):
            raise ValueError('Crypto cannot be Not applicable: activity, cash, or holdings exist. Select Applicable.')
    if not forex_applicable:
        if opening_lot_book is not None:
            for instrument in opening_lot_book.instruments():
                if re.fullmatch(r'[A-Z]{3}/[A-Z]{3}', getattr(instrument, 'symbol', '')) and opening_lot_book.lots(instrument):
                    raise ValueError('Forex cannot be Not applicable: an opening currency position exists. Supply the Forex report.')
        statement = ThinkorswimSourceReader().read(str(path))
        for section in statement.sections:
            for row in csv.reader(section.lines):
                if section.name == 'Account Trade History' and len(row) > 9 and row[9].strip().upper() == 'FOREX':
                    try:
                        when = datetime.strptime(row[1].strip(), '%m/%d/%y %H:%M:%S').date()
                    except ValueError:
                        raise ValueError('Forex execution evidence exists. Select Applicable and supply the Forex report.')
                    if period_start <= when <= period_end:
                        raise ValueError('Forex trades exist in this month. Select Applicable and supply the Forex report.')
                if 'Forex' not in section.name:
                    continue
                if row and re.fullmatch(r'[A-Z]{3}/[A-Z]{3}', row[0].strip()) and len(row) > 1 and money(row[1]) != 0:
                    raise ValueError('Forex holdings exist. Select Applicable and supply the Forex report.')
                if len(row) < 9:
                    continue
                try:
                    when = datetime.strptime(row[1].strip(), '%m/%d/%y').date()
                except ValueError:
                    continue
                if period_start <= when <= period_end and (
                        row[3].strip().upper() == 'TRD' or any(money(value) != 0 for value in row[6:])):
                    raise ValueError('Forex activity or cash exists in this month. Select Applicable and supply the Forex report.')
