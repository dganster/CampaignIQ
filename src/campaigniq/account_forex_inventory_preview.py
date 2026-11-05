"""Read-only October Forex position controls; no bootstrap, receipts or publication."""
from calendar import monthrange
import csv
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from campaigniq import account_forex_cash_review as cash
from campaigniq.broker_account_binding import verify_document
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.forex_opening_statement import (
    _amount, _coverage, _NUMBER, _POSITION, _TRADE,
    attach_forex_opening, read_forex_opening_statement, verify_forex_ending,
)
from campaigniq.importers.thinkorswim.forex_position_effect_resolver import ForexPositionEffectResolver
from campaigniq.importers.thinkorswim.forex_trade_reader import read_forex_trades
from campaigniq.importers.thinkorswim.forex_translator import to_forex_trade
from campaigniq.pdf_text import extract_pdf_text
from campaigniq.persistence.authoritative_lot_state import load_preceding_authoritative_state_from_storage, lot_state_key
from campaigniq.persistence.import_drafts import MAX_DOCUMENT_BYTES
from campaigniq.persistence.lot_book_store import serialize_lot_book
from campaigniq.persistence.monthly_publication import PROTOCOL_FILE, finalized_month_marker_key
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


MONTHLY = 'schwab_forex_monthly_statement'
POLICY = 'october_forex_statement_inventory_preview_v1'
_DETAIL = re.compile(rf'TR Details:\s+(\d{{1,2}}/\d{{1,2}}/\d{{4}}) \d{{1,2}}:\d{{2}}:\d{{2}} (?:AM|PM)\s+(Buy|Sell)\s+({_NUMBER})\s+({_NUMBER})\s*')


def pdf_bytes(upload):
    if not callable(getattr(upload, 'getvalue', None)) or getattr(upload, 'media_type', None) != 'application/pdf':
        raise ValueError('Original monthly Forex PDF required')
    raw = upload.getvalue()
    if not isinstance(raw, bytes) or not raw.startswith(b'%PDF-') or len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError('Original monthly Forex PDF at most 20 MiB required')
    return raw


def _positions_checked(section, *, earliest, latest):
    """Require totals, their original detail units, headings and closing footer."""
    headings = [line for line in section.splitlines() if line.strip().startswith('Currency Pair')]
    if len(headings) != 1 or not all(v in headings[0] for v in ('Buy/Sell', 'Quantity', 'Trade Price')):
        raise ValueError('Complete Forex position table heading required')
    if section.count('Floating Profit/Loss Net USD :') != 1:
        raise ValueError('Complete Forex position table footer required')
    active = None
    details = []
    positions = {}
    finished = False
    for line in section.splitlines():
        value = line.strip()
        if not value:
            continue
        if value.startswith('Currency Pair'):
            continue
        if value.startswith('Floating Profit/Loss Net USD :'):
            if active is not None or details:
                raise ValueError('Position detail is missing its total')
            trailing = value.split(':', 1)[1].strip()
            if trailing:
                _amount(trailing.replace('$', ''))
            finished = True
            continue
        if finished:
            raise ValueError('Unexpected content after Forex position footer')
        if re.fullmatch('[A-Z]{3}/[A-Z]{3}', value):
            ForexPair.from_symbol(value)
            if active is not None:
                raise ValueError('Forex position is missing its total')
            active = value
            continue
        detail = _DETAIL.fullmatch(value)
        if detail:
            if active is None:
                raise ValueError('Forex position detail has no pair')
            day, side, number, rate = detail.groups()
            day = datetime.strptime(day, '%m/%d/%Y').date()
            quantity = _amount(number)
            if not earliest <= day <= latest or not quantity or _amount(rate) <= 0 or (side == 'Buy') != (quantity > 0):
                raise ValueError('Forex position detail date, side, quantity or rate differs')
            details.append(quantity)
            continue
        total = _POSITION.fullmatch(value + ' ')
        # The established total grammar permits trailing floating P/L/margin columns.
        if total is None:
            total = _POSITION.match(value + ' ')
        if total:
            for value in (value+' ')[total.end():].split():
                if value != '--':
                    _amount(value.replace('$', ''))
            pair, side, number = total.groups()
            quantity = _amount(number)
            if pair != active or pair in positions or not details or not quantity or (side == 'Buy') != (quantity > 0):
                raise ValueError('Forex position total is duplicate, missing details or inconsistent')
            if sum(details, Decimal(0)) != quantity or any((q > 0) != (quantity > 0) for q in details):
                raise ValueError('Forex position details do not sum to the reported total')
            positions[pair] = quantity
            active, details = None, []
            continue
        raise ValueError('Unsupported Forex position table row')
    return positions


def statement_boundaries(text, known, *, period_start, period_end):
    pages = [p.strip() for p in re.split(r'(?=^Customer Account Summary Report since )', text.replace('\f', '\n'), flags=re.M) if p.strip()]
    if len(pages) < 2:
        raise ValueError('Monthly summary and first daily Forex report required')
    monthly_last = _coverage(pages[0])[1]
    previous_daily_end = None
    for index, page in enumerate(pages):
        verify_document(known, MONTHLY, page)
        start, end = _coverage(page)
        if index and (start >= end or end < period_start or end > monthly_last or (previous_daily_end is not None and start < previous_daily_end)):
            raise ValueError('Daily Forex report coverage overlaps or differs from the selected month')
        if index:
            previous_daily_end = end
        for line in page.splitlines():
            trade = _TRADE.fullmatch(line)
            if trade and (not index or not start <= date.fromisoformat(trade[1]) <= end):
                raise ValueError('Forex execution date differs from its daily report coverage')
    if pages[0].count('Open Position(s) at End of Month') != 1 or pages[1].count('Open Position(s)') != 1 or pages[1].count('Closed Position(s)') != 1:
        raise ValueError('Missing or ambiguous Forex boundary sections')
    opening_section = pages[1].split('Open Position(s)', 1)[1].split('Closed Position(s)', 1)[0]
    closing_section = pages[0].split('Open Position(s) at End of Month', 1)[1]
    opening = _positions_checked(opening_section, earliest=date.min, latest=period_start-date.resolution)
    closing = _positions_checked(closing_section, earliest=date.min, latest=period_end)
    return opening, closing


def monthly_projection(raw, *, period_start, period_end):
    """Select existing monthly TRD rows; never infer or repair a source execution."""
    with TemporaryDirectory(prefix='campaigniq-forex-projection-') as folder:
        path = Path(folder)/'original.csv'
        path.write_bytes(raw)
        statement = ThinkorswimSourceReader().read(str(path))
        sections = [s for s in statement.sections if s.name == 'Forex Statements']
        if len(sections) != 1:
            raise ValueError('One original Forex cash section required')
        section = sections[0]
        header = section.header()
        selected, origins = [], []
        for index, row in enumerate(section.rows(), 1):
            if len(row) != len(header) or row[3].strip() != 'TRD':
                continue
            when = datetime.strptime(row[1] + ' ' + row[2], '%m/%d/%y %H:%M:%S')
            if period_start <= when.date() <= period_end:
                selected.append(row)
                origins.append(dict(section_data_row=index, reference=row[4].strip().lstrip('=').strip('"')))
    if not selected:
        raise ValueError('No selected-month Forex executions in saved cash original')
    output = io.StringIO()
    output.write('Account Statement monthly projection for inventory validation\n\nForex Statements\n')
    csv.writer(output, lineterminator='\n').writerows([header, *selected])
    output.write('\n')
    return output.getvalue().encode(), origins


def _unique(pairs):
    body = {}
    for key, value in pairs:
        if key in body:
            raise ValueError('Duplicate publication control field')
        body[key] = value
    return body


def preceding_state(storage, *, start):
    """Use the existing exact predecessor rules, verifying any protection marker."""
    end = start-date.resolution
    key = lot_state_key(period_end=end)
    hashes = {}
    protected = False
    if storage.exists(PROTOCOL_FILE):
        raw = storage.read_text(PROTOCOL_FILE)
        protocol = json.loads(raw, object_pairs_hook=_unique)
        if (not isinstance(protocol, dict) or set(protocol) != {'format', 'version', 'first_protected_period_end'}
            or protocol['format'] != 'campaigniq.monthly_publication' or type(protocol['version']) is not int or protocol['version'] != 1):
            raise ValueError('Invalid publication protocol')
        protected = end >= date.fromisoformat(protocol['first_protected_period_end'])
        hashes[PROTOCOL_FILE] = sha256(raw.encode()).hexdigest()
    marker = finalized_month_marker_key(period_end=end)
    if protected and storage.exists(marker):
        raw = storage.read_text(marker)
        body = json.loads(raw, object_pairs_hook=_unique)
        if (not isinstance(body, dict) or set(body) != {'format', 'version', 'period_end'}
            or body['format'] != 'campaigniq.finalized_month' or type(body['version']) is not int or body['version'] != 1 or body['period_end'] != end.isoformat()):
            raise ValueError('Invalid predecessor publication marker')
        hashes[marker] = sha256(raw.encode()).hexdigest()
    loaded = load_preceding_authoritative_state_from_storage(storage, period_start=start)
    if loaded is None:
        return None, hashes
    raw = storage.read_text(key)
    body = json.loads(raw, object_pairs_hook=_unique)
    if (type(body.get('version')) is not int or body['version'] != 1
        or type(body.get('next_id')) is not int or body['next_id'] < 1):
        raise ValueError('Invalid predecessor lot-state version or sequence')
    hashes[key] = sha256(raw.encode()).hexdigest()
    book = loaded[2]
    ids = set()
    for instrument in book.instruments():
        for lot in book.lots(instrument):
            if not lot.quantity.is_finite() or not lot.quantity or lot.lot_id in ids:
                raise ValueError('Invalid predecessor lot quantity or duplicate lot identity')
            ids.add(lot.lot_id)
            if lot.basis_total is not None and not lot.basis_total.is_finite():
                raise ValueError('Invalid predecessor lot basis')
            if isinstance(instrument, ForexPair) and lot.opened_at.date() >= start:
                raise ValueError('Predecessor Forex lot was opened after the boundary')
    return book, hashes


def evaluate(service, session, draft, monthly_pdf):
    """One caller-owned read-only snapshot; the uploaded PDF is temporary only."""
    saved = cash.saved_preview(service, session, draft)
    receipt = cash.load_receipt(session, draft)
    originals = cash.originals(session, draft, receipt)
    report = session.drafts.document(draft.id, cash.REPORT)[2]
    start = date(draft.year, draft.month, 1)
    end = date(draft.year, draft.month, monthrange(draft.year, draft.month)[1])
    projection, origins = monthly_projection(originals[cash.CASH], period_start=start, period_end=end)
    known = service._known(session)
    with TemporaryDirectory(prefix='campaigniq-forex-inventory-') as folder:
        root = Path(folder)
        pdf = root/'monthly.pdf'; pdf.write_bytes(monthly_pdf)
        text = extract_pdf_text(pdf, root/'monthly.txt').read_text(encoding='utf-8')
        opening, closing = statement_boundaries(text, known, period_start=start, period_end=end)
        history = root/'monthly-projection.csv'; history.write_bytes(projection)
        csv_report = root/'report.csv'; csv_report.write_bytes(report)
        evidence = read_forex_opening_statement(text, trade_history=history, transaction_report=csv_report, period_end=end)
        if dict(evidence.opening) != opening or dict(evidence.closing) != closing:
            raise ValueError('Forex boundary readers disagree')
        rows = read_forex_trades(ThinkorswimSourceReader().read(str(history)).section('Forex Statements'))
    book, predecessor_hashes = preceding_state(session.artifacts, start=start)
    lots = []
    allocations = []
    blockers = ['Forex monetary source resolution and financial publication integration remain pending.',
                'Owner source association is not independent verification linking securities and Forex accounts.']
    if book is not None:
        before = serialize_lot_book(period_end=start-date.resolution, lot_book=book)
        clone = attach_forex_opening(book, evidence, period_start=start, authoritative=True)
        resolver = ForexPositionEffectResolver(opening)
        for row in sorted(rows, key=lambda row: row.executed_at):
            for part in resolver.resolve(pair=row.pair, quantity=row.quantity):
                for allocation in clone.apply_trade(to_forex_trade(row, part.position_effect, part.quantity)):
                    allocations.append(dict(reference=row.reference, lot_id=allocation.lot_id,
                        campaign_id=allocation.campaign_id, quantity=str(allocation.quantity)))
        verify_forex_ending(clone, evidence)
        for instrument in clone.instruments():
            if isinstance(instrument, ForexPair):
                for lot in clone.lots(instrument):
                    lots.append(dict(pair=instrument.symbol, lot_id=lot.lot_id, campaign_id=lot.campaign_id, quantity=str(lot.quantity)))
        if serialize_lot_book(period_end=start-date.resolution, lot_book=book) != before:
            raise ValueError('Inventory preview mutated the authoritative opening book')
    else:
        blockers.insert(0, 'Exact published preceding month lot state is missing; no opening lots were inferred or seeded.')
    result = dict(format='campaigniq.account_forex_inventory_preview', version=1, policy=POLICY,
        account_id=session.account.account_id, draft_id=str(draft.id), revision=draft.revision,
        period_start=start.isoformat(), period_end=end.isoformat(),
        statement_opening_quantities={p: str(q) for p, q in sorted(opening.items())},
        statement_closing_quantities={p: str(q) for p, q in sorted(closing.items())},
        statement_inventory_equation_verified=True, statement_executions_verified=True,
        authoritative_predecessor_available=book is not None, authoritative_opening_matches=book is not None,
        authoritative_replay_closing_matches=book is not None, preview_ending_lots=lots,
        preview_close_allocations=allocations, source_row_origins=origins,
        source_sha256=dict(saved['calculation']['source_sha256'], monthly_pdf=sha256(monthly_pdf).hexdigest(),
            monthly_extracted_text=sha256(text.encode()).hexdigest(), monthly_execution_projection=sha256(projection).hexdigest(),
            owner_source_receipt=saved['receipt_sha256']), predecessor_artifact_sha256=predecessor_hashes,
        original_cash_review=saved, broker_verified_cross_account_link=False,
        monthly_pdf_saved=False, financial_state_written=False, publication_authorized=False, blockers=blockers)
    result['review_sha256'] = sha256(cash.canonical(result)).hexdigest()
    return result
