"""Rebuild a daily-session monetary candidate from verified saved originals."""
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq import account_forex_daily_sources as sources
from campaigniq import account_forex_cash_review as cash
from campaigniq.account_forex_inventory_preview import preceding_state
from campaigniq.forex_daily_financial_candidate import calculate, canonical, POLICY
from campaigniq.pdf_text import extract_pdf_text


def build_record(service, session, draft):
    saved = sources.saved_preview(service, session, draft)
    if not saved['combined_inventory_evidence_reconciled']:
        raise ValueError('Published opening and saved daily inventory evidence must reconcile first')
    receipt = sources.load_receipt(session, draft)
    pdfs = sources.saved_originals(session, draft, receipt)
    cash_receipt = cash.load_receipt(session, draft)
    originals = cash.originals(session, draft, cash_receipt)
    book, predecessor = preceding_state(session.artifacts, start=date(2026,10,1))
    if book is None: raise ValueError('Exact published predecessor required; no opening lots are seeded')
    with TemporaryDirectory(prefix='campaigniq-forex-financial-candidate-') as folder:
        root = Path(folder); reports = []
        for index, (name, raw) in enumerate(pdfs):
            path = root/(str(index)+'.pdf'); path.write_bytes(raw)
            text = extract_pdf_text(path, root/(str(index)+'.txt')).read_text(encoding='utf-8')
            reports.append(dict(name=name,text=text,pdf_sha256=sha256(raw).hexdigest()))
        ledger = root/'cash.csv'; ledger.write_bytes(originals[cash.CASH])
        report = root/'report.csv'; report.write_bytes(session.drafts.document(draft.id,cash.REPORT)[2])
        result = calculate(reports, known=service._known(session), cash_ledger=ledger,
            transaction_report=report, opening_book=book)
    expected = saved['original_cash_review']['calculation']['source_sha256']
    if (result['source_sha256']['cash_ledger'] != expected['cash_ledger'] or
        result['source_sha256']['transaction_report'] != expected['transaction_report'] or
        predecessor != saved['predecessor_artifact_sha256']):
        raise ValueError('Financial candidate differs from verified saved-source context')
    return dict(format='campaigniq.account_forex_financial_candidate',version=1,
        account_id=session.account.account_id,draft_id=str(draft.id),source_revision=draft.revision,
        policy_id=POLICY,calculation=result,
        daily_source_receipt_sha256=saved['daily_source_receipt_sha256'],
        cash_source_receipt_sha256=saved['original_cash_review']['receipt_sha256'],
        predecessor_artifact_sha256=predecessor,binding_sha256=receipt['binding_sha256'],
        original_source_sha256=expected,
        original_cash_source_differences=saved['original_cash_review']['calculation'],
        source_receipts_verified=True,financial_policy_approved=False,
        financial_state_written=False,publication_authorized=False)


def preview(service, session, draft):
    body = build_record(service, session, draft)
    text = canonical(body)+'\n'
    return dict(format='campaigniq.account_forex_financial_preview',version=1,
        artifact=body,artifact_text=text,artifact_sha256=sha256(text.encode()).hexdigest(),
        candidate_only=True,financial_state_written=False,publication_authorized=False)


def verify_record(service, session, draft, text):
    # Rebuilding within the current authorized snapshot validates scope,
    # receipts, original bytes, canonical opening and every monetary equation.
    body = json.loads(text, object_pairs_hook=sources.unique)
    if canonical(body)+'\n' != preview(service,session,draft)['artifact_text']:
        raise ValueError('Financial candidate differs from current verified originals or scope')
    return body
