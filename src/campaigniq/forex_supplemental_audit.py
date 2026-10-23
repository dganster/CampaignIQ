"""Development-only, read-only comparison of supplemental monthly Forex evidence.

This diagnostic does not replace the standard CSV, change import policy, repair
transactions, authorize exceptions, or establish full backfill continuity.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from campaigniq.importers.schwab.forex_transaction_reader import read_forex_transaction_report
from campaigniq.importers.thinkorswim.forex_trade_reader import read_forex_trades
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader
from campaigniq.pdf_text import extract_pdf_text

_PAIR = r'[A-Z]{3}/[A-Z]{3}'
_NUMBER = r'\(?[+-]?[\d,]+(?:\.\d+)?\)?'
_HEADER = re.compile(
    r'^Customer Account Summary Report since \w+, (?P<start>\w+ \d+, \d{4}) '
    r'16:00:00 \(CST\) through \w+, (?P<end>\w+ \d+, \d{4}) 16:00:00 \(CST\)$')
_TRADE = re.compile(
    rf'^\s*(\d{{4}}-\d{{2}}-\d{{2}}) \d{{2}}:\d{{2}}:\d{{2}}\s+'
    rf'({_PAIR})\s+\S+\s+([BS])\s+(OPEN|CLOSE)\s+({_NUMBER})\s+'
    rf'({_NUMBER})\s+(\d+)\s+\$({_NUMBER})\s*$')
_POSITION = re.compile(rf'^({_PAIR}) - TOTAL POSITION:\s+(Buy|Sell)\s+({_NUMBER})\s+')


def _amount(text):
    text = text.strip().replace(',', '').replace('$', '')
    value = -Decimal(text[1:-1]) if text.startswith('(') and text.endswith(')') else Decimal(text)
    if not value.is_finite():
        raise ValueError('Non-finite evidence quantity or amount.')
    return value


def _last_weekday(end):
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    return end


@dataclass(frozen=True)
class MonthlyForexEvidence:
    period_start: date
    statement_end: date
    positions: tuple[tuple[str, Decimal], ...]
    # order id, calendar trade date, pair, signed quantity, price
    trades: tuple[tuple[str, str, str, Decimal, Decimal], ...]
    settled_pl: Decimal
    financing: Decimal
    sha256: str


def read_monthly_forex_evidence(text: str, *, period_end: date) -> MonthlyForexEvidence:
    """Read the supported monthly summary and explicit daily trade-detail rows.

    A weekend month-end may use the last weekday. Holidays, other layouts and
    ambiguous or incomplete sections fail closed rather than guessing coverage.
    Dates are compared at calendar-day granularity; time-zone equivalence is not
    asserted by this diagnostic.
    """
    if period_end.month == 12:
        following = date(period_end.year + 1, 1, 1)
    else:
        following = date(period_end.year, period_end.month + 1, 1)
    if period_end != following - timedelta(days=1):
        raise ValueError('Expected a month-end date.')
    pages = [p for p in text.split('\f') if p.strip()]
    if not pages:
        raise ValueError('Monthly statement is empty.')
    first = pages[0]
    header = _HEADER.fullmatch(first.splitlines()[0].strip())
    if header is None or 'Monthly Equity Summary' not in first:
        raise ValueError('Expected a supported monthly Forex summary.')
    start = datetime.strptime(header['start'], '%B %d, %Y').date()
    end = datetime.strptime(header['end'], '%B %d, %Y').date()
    if start != period_end.replace(day=1) - timedelta(days=1) or end != _last_weekday(period_end):
        raise ValueError('Monthly Forex statement coverage differs from the selected month.')
    if first.count('Open Position(s) at End of Month') != 1:
        raise ValueError('Missing or ambiguous month-end positions section.')
    section = first.split('Open Position(s) at End of Month', 1)[1]
    if 'Floating Profit/Loss Net USD' not in section:
        raise ValueError('Incomplete month-end positions section.')
    positions = {}
    for line in section.splitlines():
        if '- TOTAL POSITION:' not in line:
            continue
        match = _POSITION.match(line.strip())
        if match is None:
            raise ValueError('Unrecognized month-end position row.')
        pair, side, quantity = match.groups()
        value = _amount(quantity)
        if value == 0 or (side == 'Buy' and value < 0) or (side == 'Sell' and value > 0):
            raise ValueError('Month-end position side disagrees with quantity.')
        if pair in positions:
            raise ValueError('Duplicate month-end position pair.')
        positions[pair] = value
    # A recognizable empty summary is legitimate; malformed numeric rows are not.
    if not positions and re.search(r'TR Details:|TOTAL POSITION', section):
        raise ValueError('Unparsed month-end position details.')

    def summary(label):
        found = re.findall(rf'^\s*{re.escape(label)}\s+(\(?[+-]?\$[\d,]+(?:\.\d+)?\)?)(?:\s{{2,}}.*)?\s*$', first, re.M)
        if len(found) != 1:
            raise ValueError('Missing or ambiguous monthly summary amount: ' + label)
        return _amount(found[0])

    trades = []
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == 'Trade Activity Detail':
            inside = True
            continue
        if stripped.startswith(('Financing Report', 'Conversion Report', 'Customer Account Summary Report')):
            inside = False
        if not inside:
            continue
        match = _TRADE.fullmatch(line)
        if match is None:
            if re.match(r'\s*\d{4}-\d{2}-\d{2}', line):
                raise ValueError('Unrecognized monthly trade-detail row.')
            continue
        day, pair, side, _effect, quantity, price, order_id, _markup = match.groups()
        trade_day = date.fromisoformat(day)
        if not period_end.replace(day=1) <= trade_day <= end:
            raise ValueError('Monthly trade detail falls outside the selected period.')
        quantity, price = _amount(quantity), _amount(price)
        if quantity == 0 or price <= 0 or (side == 'B' and quantity < 0) or (side == 'S' and quantity > 0):
            raise ValueError('Monthly trade side, quantity or price is invalid.')
        row = (order_id, day, pair, quantity, price)
        if row in trades:
            raise ValueError('Duplicate monthly execution evidence.')
        trades.append(row)
    if 'Trade Activity Detail' not in text:
        raise ValueError('Monthly trade-detail evidence is absent.')
    return MonthlyForexEvidence(start, end, tuple(sorted(positions.items())), tuple(trades),
        summary('Settled Profit/Loss'), summary('Financing'), sha256(text.encode()).hexdigest())


def audit_forex_supplement(*, monthly_text: str, transaction_report: Path,
                          trade_history: Path, period_end: date,
                          computed_positions: dict[str, Decimal] | None = None):
    """Compare three unmodified sources; mismatches remain explicit blockers."""
    monthly = read_monthly_forex_evidence(monthly_text, period_end=period_end)
    report_bytes, history_bytes = transaction_report.read_bytes(), trade_history.read_bytes()
    report = read_forex_transaction_report(transaction_report)
    if (report.period_start != monthly.period_start or report.period_end != period_end):
        raise ValueError('Transaction Report must use the standard selected-month interval.')
    history = read_forex_trades(ThinkorswimSourceReader().read(str(trade_history)).section('Forex Statements'))
    first_day = period_end.replace(day=1)
    history_rows = Counter((r.reference, r.executed_at.date().isoformat(), r.pair, r.quantity, r.price)
        for r in history if first_day <= r.executed_at.date() <= period_end)
    report_rows = Counter((r.order_id, r.trade_at.date().isoformat(), r.instrument, r.amount, r.rate)
        for r in (*report.new_transactions, *report.settlements))
    if any(not first_day <= r.trade_at.date() <= period_end
           for r in (*report.new_transactions, *report.settlements)):
        raise ValueError('CSV includes trades outside the selected month.')
    statement_rows = Counter(monthly.trades)
    def rows(counter):
        return [dict(order_id=x[0], date=x[1], pair=x[2], quantity=str(x[3]), rate=str(x[4]))
                for x in sorted(counter.elements())]
    findings = []
    if history_rows != statement_rows:
        findings.append('Monthly execution evidence and trade history differ.')
    if report_rows != statement_rows:
        findings.append('Transaction Report execution evidence differs from monthly statement.')
    pl_delta = monthly.settled_pl - report.settlement_pl_usd
    financing_delta = monthly.financing - report.financing_usd
    if pl_delta:
        findings.append('Monthly settled profit/loss differs from CSV settlement rows.')
    if financing_delta:
        findings.append('Monthly financing differs from CSV financing rows.')
    closing_matches = None
    if computed_positions is not None:
        computed = {}
        for pair, quantity in computed_positions.items():
            if not re.fullmatch(_PAIR, pair) or not isinstance(quantity, Decimal) or not quantity.is_finite():
                raise ValueError('Invalid computed Forex position.')
            if quantity:
                computed[pair] = quantity
        closing_matches = computed == dict(monthly.positions)
        if not closing_matches:
            findings.append('Computed Forex closing quantities differ from monthly statement.')
    if report_bytes != transaction_report.read_bytes() or history_bytes != trade_history.read_bytes():
        raise ValueError('Source evidence changed during audit.')
    return {
        'format': 'campaigniq.forex_supplemental_audit', 'version': 1,
        'period_end': period_end.isoformat(), 'monthly_statement_end': monthly.statement_end.isoformat(),
        'monthly_closing_positions': {k:str(v) for k,v in monthly.positions},
        'monthly_trade_count': len(monthly.trades),
        'monthly_vs_history_match': history_rows == statement_rows,
        'monthly_vs_csv_match': report_rows == statement_rows,
        'monthly_trades_missing_from_csv': rows(statement_rows - report_rows),
        'csv_trades_missing_from_monthly': rows(report_rows - statement_rows),
        'monthly_trades_missing_from_history': rows(statement_rows - history_rows),
        'history_trades_missing_from_monthly': rows(history_rows - statement_rows),
        'monthly_settled_pl_usd': str(monthly.settled_pl),
        'csv_settlement_pl_usd': str(report.settlement_pl_usd),
        'settled_pl_difference_usd': str(pl_delta),
        'monthly_financing_usd': str(monthly.financing), 'csv_financing_usd': str(report.financing_usd),
        'financing_difference_usd': str(financing_delta),
        'computed_closing_positions_match': closing_matches,
        'source_sha256': {'monthly_extracted_text': monthly.sha256,
            'transaction_report':sha256(report_bytes).hexdigest(), 'trade_history':sha256(history_bytes).hexdigest()},
        'blocking_findings': findings, 'publication_authorized': False,
        'full_continuity_verified': False,
        'limitation': 'Diagnostic only; does not alter imports, standard input policy, source files, positions or publication. Dates compared by calendar day; PDF authenticity, source completeness, campaign identity and full continuity remain unverified.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only', action='store_true', required=True)
    parser.add_argument('--period-end', type=date.fromisoformat, required=True)
    parser.add_argument('--monthly-statement', type=Path, required=True)
    parser.add_argument('--transaction-report', type=Path, required=True)
    parser.add_argument('--trade-history', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        parser.error('Development runner must not execute on Render.')
    content = args.monthly_statement.read_bytes()
    with TemporaryDirectory(prefix='campaigniq-forex-audit-') as directory:
        copied = Path(directory)/'monthly.pdf'
        copied.write_bytes(content)
        extracted = extract_pdf_text(copied, Path(directory)/'monthly.txt')
        result = audit_forex_supplement(monthly_text=extracted.read_text(),
            transaction_report=args.transaction_report, trade_history=args.trade_history,
            period_end=args.period_end)
    if args.monthly_statement.read_bytes() != content:
        raise ValueError('Monthly PDF changed during audit.')
    result['source_sha256']['monthly_pdf'] = sha256(content).hexdigest()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
