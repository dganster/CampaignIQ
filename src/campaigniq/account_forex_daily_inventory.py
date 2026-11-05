"""Temporary original daily-PDF archive review in one authorized read-only snapshot."""
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import io
from pathlib import Path, PurePosixPath
import stat
from tempfile import TemporaryDirectory
import zipfile

from campaigniq import account_forex_cash_review as cash
from campaigniq.account_forex_inventory_preview import preceding_state
from campaigniq.forex_cash_ledger_preview import _ledger, money
from campaigniq.forex_daily_inventory import validate_daily_chain
from campaigniq.importers.thinkorswim.forex_trade_reader import read_forex_trades
from campaigniq.pdf_text import extract_pdf_text
from campaigniq.persistence.import_drafts import MAX_DOCUMENT_BYTES
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader

MAX_ARCHIVE_BYTES = 64*1024*1024


def archive_bytes(upload):
    if not callable(getattr(upload, 'getvalue', None)) or getattr(upload, 'media_type', None) not in ('application/zip', 'application/x-zip-compressed'):
        raise ValueError('ZIP of original daily Forex PDFs required')
    raw = upload.getvalue()
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_ARCHIVE_BYTES:
        raise ValueError('Daily PDF archive must be nonempty and at most 64 MiB')
    originals(raw)  # Reject unsafe/unsupported entries before opening a database transaction.
    return raw


def originals(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > 100 or sum(i.file_size for i in infos) > MAX_ARCHIVE_BYTES or len({i.filename for i in infos}) != len(infos):
                raise ValueError('Daily archive size or inventory is invalid')
            result = []
            for item in infos:
                name = PurePosixPath(item.filename)
                if name.is_absolute() or '..' in name.parts or '\\' in item.filename or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                    raise ValueError('Unsafe or encrypted daily archive entry')
                if item.is_dir():
                    continue
                if name.suffix.lower() != '.pdf' or not 0 < item.file_size <= MAX_DOCUMENT_BYTES:
                    raise ValueError('Daily archive must contain original PDFs only, at most 20 MiB each')
                content = archive.read(item)
                if not content.startswith(b'%PDF-'):
                    raise ValueError('Daily archive entry is not an original PDF')
                result.append((item.filename, content))
            if not 1 <= len(result) <= 31:
                raise ValueError('Between one and 31 original daily reports required')
            return result
    except (zipfile.BadZipFile, RuntimeError) as error:
        raise ValueError('Daily Forex archive is corrupt or unsupported') from error


def evaluate(service, session, draft, archive):
    saved = cash.saved_preview(service, session, draft)
    receipt = cash.load_receipt(session, draft)
    docs = cash.originals(session, draft, receipt)
    start, end = date(2026, 10, 1), date(2026, 10, 31)
    book, predecessor_hashes = preceding_state(session.artifacts, start=start)
    with TemporaryDirectory(prefix='campaigniq-forex-daily-review-') as folder:
        root = Path(folder)
        reports = []
        for index, (name, raw) in enumerate(originals(archive)):
            # Archive paths are labels only, never extraction destinations.
            path = root/(str(index)+'.pdf'); path.write_bytes(raw)
            text = extract_pdf_text(path, root/(str(index)+'.txt')).read_text(encoding='utf-8')
            reports.append(dict(name=name, text=text, pdf_sha256=sha256(raw).hexdigest()))
        ledger = root/'cash.csv'; ledger.write_bytes(docs[cash.CASH])
        section = ThinkorswimSourceReader().read(str(ledger)).section('Forex Statements')
        rows = [r for r in read_forex_trades(section) if start <= r.executed_at.date() <= end]
        result = validate_daily_chain(reports, known=service._known(session), period_start=start, period_end=end,
            opening_book=book, history_rows=rows)
        monthly_cash = _ledger(ledger, end)
    boundary = date.fromisoformat(result['last_independent_daily_boundary'])
    tail = [row for when, row, ref in monthly_cash['selected'] if when.date() > boundary]
    tail_unchanged = (monthly_cash['closing'] == Decimal(result['last_daily_cash_usd'])
        and all(row[3].strip() != 'TRD' and money(row[8]) == 0 for row in tail))
    independent = result['full_independent_daily_coverage']
    result.update(account_id=session.account.account_id, draft_id=str(draft.id), revision=draft.revision,
        archive_sha256=sha256(archive).hexdigest(), predecessor_artifact_sha256=predecessor_hashes,
        original_cash_review=saved, ledger_tail_no_activity_recorded=tail_unchanged,
        export_month_end_cash_usd=str(monthly_cash['closing']), export_month_end_date=end.isoformat(),
        export_supported_ending_quantities=result['last_daily_closing_quantities'] if tail_unchanged else None,
        combined_inventory_evidence_reconciled=bool(result['authoritative_opening_matches'] and tail_unchanged),
        independent_month_end_positions_verified=bool(independent and result['authoritative_opening_matches']),
        daily_vs_candidate_trading_delta_usd=str(Decimal(saved['calculation']['candidate_trading_usd'])-Decimal(result['daily_settled_pl_usd'])),
        original_csv_mtd_minus_daily_settled_usd=str(Decimal(saved['calculation']['csv_mtd_settled_pl_usd'])-Decimal(result['daily_settled_pl_usd'])),
        daily_vs_candidate_financing_delta_usd=str(Decimal(saved['calculation']['candidate_financing_usd'])-Decimal(result['daily_financing_usd'])),
        daily_start_cash_minus_export_opening_cash_usd=str(Decimal(result['opening_cash_usd'])-Decimal(saved['calculation']['opening_cash_usd'])),
        daily_pdfs_saved=False, broker_verified_cross_account_link=False,
        blockers=['Forex daily originals/owner receipts and financial candidate/writer integration remain pending.',
                  'Original CSV monetary differences remain retained; this review grants no accounting or publication approval.',
                  'Daily session and cash-export calendar cutoffs differ; dollar comparisons are diagnostic and do not activate a source policy.'])
    if not independent:
        result['blockers'].append('Independent daily confirmation stops at '+str(boundary)+'. Later unchanged export balances are labelled separately; reports are not invented or presumed to have been issued.')
    if not tail_unchanged:
        result['blockers'].append('Cash-export activity or ending balance after the final daily confirmation requires further evidence.')
    if book is None:
        result['blockers'].append('Exact published predecessor is missing; no authoritative opening lots were seeded.')
    result['review_sha256'] = sha256(cash.canonical(result)).hexdigest()
    return result
