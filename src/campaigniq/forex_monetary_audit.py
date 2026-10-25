"""Development-only Forex money evidence audit; no import/publication operations.

The user-approved USD 0.01 tolerance applies ONLY to aggregate monetary controls.
Individual executions, quantities, rates, financing IDs/amounts and provenance
remain exact. Differences are reported without changing any broker value.
"""
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import json
import re
import subprocess

from campaigniq.forex_supplemental_audit import (
    _amount, _TRADE, audit_forex_supplement, read_monthly_forex_evidence,
)
from campaigniq.historical_backfill_ancestry import _development
from campaigniq.importers.schwab.forex_transaction_reader import read_forex_transaction_report, oid
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader

SUMMARY_TOLERANCE_USD = Decimal('0.01')
_MONEY = r'\(?[+-]?\$[\d,]+(?:\.\d+)?\)?'


def summary_control(reported, calculated):
    """Fixed cent tolerance, never configurable into a blanket waiver."""
    if any(not isinstance(x, Decimal) or not x.is_finite() for x in (reported, calculated)):
        raise ValueError('Summary controls require finite Decimal values.')
    difference = reported - calculated
    return {'reported_usd': str(reported), 'calculated_usd': str(calculated),
        'difference_usd': str(difference), 'tolerance_usd': str(SUMMARY_TOLERANCE_USD),
        'within_tolerance': abs(difference) <= SUMMARY_TOLERANCE_USD,
        'rounding_applied': Decimal(0) < abs(difference) <= SUMMARY_TOLERANCE_USD}


def _summary(first_page, label):
    values = re.findall(rf'^\s*{re.escape(label)}\s+({_MONEY})(?:\s{{2,}}.*)?\s*$', first_page, re.M)
    if len(values) != 1:
        raise ValueError('Missing or ambiguous monthly summary: ' + label)
    return _amount(values[0])


def _monthly_money(text):
    first = text.split('\f')[0]
    if first.count('Monthly Profit/Loss Summary') != 1:
        raise ValueError('Monthly pair summary is absent or ambiguous.')
    section = first.split('Monthly Profit/Loss Summary')[1].split('Open Position(s) at End of Month')[0]
    pairs = {}
    for line in section.splitlines():
        if re.match(r'^\s*[A-Z]{3}/[A-Z]{3}', line):
            match = re.fullmatch(rf'\s*([A-Z]{{3}}/[A-Z]{{3}})\s+({_MONEY})(?:\s+{_MONEY})?\s*', line)
            if match is None or match[1] in pairs:
                raise ValueError('Malformed or duplicate monthly pair monetary row.')
            pairs[match[1]] = _amount(match[2])
    if not pairs:
        raise ValueError('Monthly pair monetary rows are absent.')
    finance = {}
    inside = False
    for line in text.splitlines():
        s = line.strip()
        if s == 'Financing Report':
            inside = True
            continue
        if s.startswith(('Conversion Report', 'Customer Account Summary Report')):
            inside = False
        if not inside or not re.match(r'^\d{4}-', s):
            continue
        parts = s.split()
        if len(parts) != 8 or not re.fullmatch(r'[A-Z]{3}/[A-Z]{3}', parts[2]) or not parts[5].isdigit():
            raise ValueError('Unrecognized monthly financing row.')
        datetime.fromisoformat(parts[0] + 'T' + parts[1])
        for value in (parts[3], parts[4], parts[6]):
            _amount(value)
        identifier = parts[5]
        if identifier in finance:
            raise ValueError('Duplicate monthly financing ID.')
        finance[identifier] = {'date': parts[0], 'pair': parts[2], 'amount': _amount(parts[7])}
    return pairs, finance


def _history_cash(path, period_end):
    statement = ThinkorswimSourceReader().read(str(path))
    section = statement.section('Forex Statements')
    columns = {c.strip(): i for i, c in enumerate(section.header()) if c.strip()}
    if not {'Date', 'Type', 'Ref #', 'Description', 'Amount(USD)'} <= columns.keys():
        raise ValueError('Unsupported Forex Statements money columns.')
    rows = {}
    for row in section.rows():
        if not row or len(row) <= max(columns.values()):
            continue
        if row[columns['Type']].strip() != 'TRD':
            continue
        day = datetime.strptime(row[columns['Date']].strip(), '%m/%d/%y').date()
        if not period_end.replace(day=1) <= day <= period_end:
            continue
        identifier = oid(row[columns['Ref #']])
        if not identifier.isdigit() or identifier in rows:
            raise ValueError('Missing or duplicate Forex history execution reference.')
        value = row[columns['Amount(USD)']].strip()
        rows[identifier] = None if value in {'', '--'} else _amount(value)
    cash_interest = {}
    try:
        cash = statement.section('Cash Balance')
    except (KeyError, ValueError):
        return rows, cash_interest
    columns = {c.strip().upper(): i for i, c in enumerate(cash.header()) if c.strip()}
    if not {'DATE', 'TYPE', 'REF #', 'DESCRIPTION', 'AMOUNT'} <= columns.keys():
        return rows, cash_interest
    for row in cash.rows():
        if not row or len(row) <= max(columns.values()):
            continue
        if row[columns['TYPE']].strip() != 'DOI' or not row[columns['DESCRIPTION']].strip().startswith('SCHWAB1 INT '):
            continue
        day = datetime.strptime(row[columns['DATE']].strip(), '%m/%d/%y').date()
        if period_end.replace(day=1) <= day <= period_end:
            identifier = oid(row[columns['REF #']])
            if identifier in cash_interest:
                raise ValueError('Duplicate cash interest reference.')
            cash_interest[identifier] = _amount(row[columns['AMOUNT']])
    return rows, cash_interest


def audit_forex_money(*, monthly_text, transaction_report, trade_history,
                     period_end, development_only=False):
    """Explain source differences; keep incomplete CSVs blocked for import."""
    _development(development_only)
    transaction_report, trade_history = Path(transaction_report), Path(trade_history)
    before = (transaction_report.read_bytes(), trade_history.read_bytes())
    account = re.findall(rf'^\s*Ending Cash Balance\s+{_MONEY}\s+(\d+)\s+USD\s*$',
                         monthly_text.split('\f')[0], re.M)
    csv_account = re.findall(r'Account \(USD\):\s*(\d+)', before[0].decode('utf-8-sig'))
    if len(account) != 1 or len(csv_account) != 1 or account != csv_account:
        raise ValueError('Monthly Forex statement and CSV must identify the same account.')
    monthly = read_monthly_forex_evidence(monthly_text, period_end=period_end)
    # Existing exact execution comparisons are preserved, including rates and
    # quantities. The tolerance below never changes that comparison.
    execution = audit_forex_supplement(monthly_text=monthly_text,
        transaction_report=transaction_report, trade_history=trade_history, period_end=period_end)
    report = read_forex_transaction_report(transaction_report)
    pairs, finance = _monthly_money(monthly_text)
    if any(not monthly.period_start <= date.fromisoformat(v['date']) <= monthly.statement_end
           for v in finance.values()):
        raise ValueError('Monthly financing date is outside statement coverage.')
    history_pl, cash_interest = _history_cash(trade_history, period_end)
    closes = {}
    for line in monthly_text.splitlines():
        match = _TRADE.fullmatch(line)
        if match is not None and match[4] == 'CLOSE':
            if match[7] in closes:
                raise ValueError('Duplicate monthly closing order ID.')
            closes[match[7]] = match[2]
    history_pairs = dict.fromkeys(pairs, Decimal(0))
    for identifier, pair in closes.items():
        if pair not in history_pairs or history_pl.get(identifier) is None:
            raise ValueError('Monthly closing execution lacks explicit matching history USD profit/loss.')
        history_pairs[pair] += history_pl[identifier]
    first = monthly_text.split('\f')[0]
    controls = {
        'monthly_settled_total_vs_pairs': summary_control(monthly.settled_pl, sum(pairs.values(), Decimal(0))),
        'monthly_financing_vs_details': summary_control(monthly.financing, sum((v['amount'] for v in finance.values()), Decimal(0))),
        'monthly_settled_total_vs_history': summary_control(monthly.settled_pl, sum(history_pairs.values(), Decimal(0))),
    }
    for pair in pairs:
        controls['monthly_pair_vs_history:' + pair] = summary_control(pairs[pair], history_pairs[pair])
    balance = (_summary(first, 'Beginning Cash Balance') + _summary(first, 'Deposits')
        - _summary(first, 'Withdrawals') + _summary(first, 'Adjustments')
        + monthly.financing + monthly.settled_pl)
    controls['monthly_cash_balance_bridge'] = summary_control(_summary(first, 'Ending Cash Balance'), balance)
    if report.pl_total_usd is not None:
        controls['csv_pl_total_vs_rows'] = summary_control(report.pl_total_usd, report.settlement_pl_usd)
    # MTD vs the independently corroborated monthly total is also an aggregate
    # check: a 38-cent disagreement cannot be treated as one-cent rounding.
    controls['csv_mtd_vs_monthly_total'] = summary_control(report.mtd_settled_pl_usd, monthly.settled_pl)
    csv_finance = {}
    for row in report.financing:
        if row.order_id in csv_finance:
            raise ValueError('Duplicate CSV financing ID.')
        csv_finance[row.order_id] = row
    changed = [identifier for identifier, value in finance.items()
        if identifier in csv_finance and value['amount'] != csv_finance[identifier].financing_usd]
    absent = sorted(set(finance) - set(csv_finance))
    extras = []
    for identifier in sorted(set(csv_finance) - set(finance)):
        row = csv_finance[identifier]
        corroborated = (row.instrument == 'US$' and cash_interest.get(identifier) == row.financing_usd)
        extras.append({'reference': identifier, 'amount_usd': str(row.financing_usd),
            'date': row.occurred_at.isoformat(), 'instrument': row.instrument,
            'classification': 'cash_interest_in_securities_cash_section' if corroborated else 'absent_from_monthly_statement',
            'excluded_from_import': False})
    settlements = {}
    for row in report.settlements:
        if row.order_id in settlements:
            raise ValueError('Duplicate CSV settlement ID.')
        settlements[row.order_id] = row
    missing_closes = []
    differences = []
    for identifier, pair in closes.items():
        amount = history_pl[identifier]
        if identifier not in settlements:
            missing_closes.append({'reference': identifier, 'pair': pair, 'history_pl_usd': str(amount)})
        elif amount != settlements[identifier].settlement_pl_usd:
            row = settlements[identifier]
            differences.append({'reference': identifier, 'pair': pair,
                'csv_pl_usd': str(row.settlement_pl_usd), 'history_pl_usd': str(amount),
                'difference_usd': str(amount - row.settlement_pl_usd)})
    findings = []
    if not execution['monthly_vs_history_match']:
        findings.append('Monthly executions and history differ; no quantity/rate tolerance applies.')
    if not execution['monthly_vs_csv_match']:
        findings.append('CSV executions differ from corroborated monthly/history evidence.')
    for name, control in controls.items():
        if not control['within_tolerance']:
            findings.append('Monetary summary exceeds USD 0.01 tolerance: ' + name)
    if absent or changed:
        findings.append('Monthly financing IDs/amounts do not match CSV exactly.')
    if extras:
        findings.append('CSV contains additional financing-classified rows; classification does not authorize exclusion.')
    if differences:
        findings.append('Individual settlement USD amounts differ; no per-transaction tolerance applies.')
    if before != (transaction_report.read_bytes(), trade_history.read_bytes()):
        raise ValueError('Source files changed during monetary audit.')
    monthly_verified = (execution['monthly_vs_history_match'] and all(c['within_tolerance']
        for name, c in controls.items() if name.startswith('monthly_')))
    return {'format': 'campaigniq.forex_money_audit', 'version': 1, 'diagnostic_only': True,
        'period_end': period_end.isoformat(),
        'rounding_policy': {'summary_tolerance_usd': str(SUMMARY_TOLERANCE_USD),
            'scope': 'Aggregate Forex monetary controls only; all exact differences retained.',
            'execution_quantity_rate_tolerance': '0', 'individual_financing_settlement_tolerance_usd': '0'},
        'monthly_money_controls_verified': monthly_verified,
        'summary_controls': controls, 'monthly_financing_count': len(finance),
        'monthly_financing_missing_from_csv': absent, 'changed_financing_ids': changed,
        'additional_csv_financing_rows': extras,
        'missing_csv_closes': missing_closes,
        'missing_close_pl_usd': str(sum((history_pl[i] for i in closes if i not in settlements), Decimal(0))),
        'settlement_usd_differences': differences,
        'execution_comparison': {'monthly_vs_history_match': execution['monthly_vs_history_match'],
            'monthly_vs_csv_match': execution['monthly_vs_csv_match'],
            'missing_csv_executions': execution['monthly_trades_missing_from_csv']},
        'source_sha256': execution['source_sha256'], 'blocking_findings': findings,
        'csv_ready_for_import': False, 'publication_authorized': False,
        'full_continuity_verified': False,
        'limitation': 'No evidence replacement, transaction repair, import-policy change or publication. Financing cutoff/time-zone equivalence and USD conversion differences are not approved automatically.'}


def main():
    import argparse
    from tempfile import TemporaryDirectory
    from campaigniq.pdf_text import extract_pdf_text
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only', required=True, action='store_true')
    parser.add_argument('--period-end', required=True, type=date.fromisoformat)
    parser.add_argument('--monthly-statement', required=True, type=Path)
    parser.add_argument('--transaction-report', required=True, type=Path)
    parser.add_argument('--trade-history', required=True, type=Path)
    args = parser.parse_args()
    try:
        _development(args.development_only)
        content = args.monthly_statement.read_bytes()
        with TemporaryDirectory(prefix='campaigniq-forex-money-') as directory:
            copied = Path(directory) / 'monthly.pdf'; copied.write_bytes(content)
            text = extract_pdf_text(copied, Path(directory) / 'monthly.txt').read_text()
            result = audit_forex_money(monthly_text=text, transaction_report=args.transaction_report,
                trade_history=args.trade_history, period_end=args.period_end, development_only=True)
        if content != args.monthly_statement.read_bytes():
            raise ValueError('Monthly PDF changed during monetary audit.')
        result['source_sha256']['monthly_pdf'] = sha256(content).hexdigest()
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': type(error).__name__, 'message': str(error), 'publication_authorized': False}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
