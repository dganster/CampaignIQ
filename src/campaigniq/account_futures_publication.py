"""Internal generation writer, exercised only by the disposable local rehearsal.

The public deployment publisher keeps its futures guard closed. Callers of this
internal function must own the authorized serialized publication transaction.
"""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq.account_futures_generation import build_candidate,verify_published
from campaigniq.account_local_publication import _publish_in_session
from campaigniq.futures_monthly_preview import monthly_preview
from campaigniq.persistence.futures_month import reference


class GenerationBuffer:
    """An in-memory candidate over an immutable view of existing financial data."""
    def __init__(self,reader):self.reader=reader;self.rows={}
    @property
    def namespace(self):return self.reader.namespace
    def list_keys(self,*,prefix='',suffix=''):
        return tuple(sorted(k for k in set(self.reader.list_keys())|self.rows.keys() if k.startswith(prefix) and k.endswith(suffix)))
    def exists(self,key):return key in self.rows or self.reader.exists(key)
    def read_text(self,key):return self.rows[key] if key in self.rows else self.reader.read_text(key)
    def write_text(self,key,text):
        if self.reader.exists(key):raise PermissionError('Existing financial records cannot be replaced')
        self.rows[key]=text
    def delete(self,key):
        if self.reader.exists(key):raise PermissionError('Existing financial records cannot be deleted')
        self.rows.pop(key,None)


def _publish_futures_in_session(service,session,actor_id,account_id,draft_id,revision,*,forex_applicable,crypto_applicable):
    """No connection creation, authority grant or commit; all writes use caller's SQL transaction."""
    if type(revision) is not int or any(type(f) is not bool for f in (forex_applicable,crypto_applicable)):
        raise ValueError('Exact revision and explicit market choices required')
    draft=service._lock(session,draft_id,write=True)
    if draft.revision!=revision:raise ValueError('Draft changed; reload before publication')
    checked=monthly_preview(service,session,draft,forex_applicable=forex_applicable,crypto_applicable=crypto_applicable)
    if not checked['all_source_controls_ready']:
        raise ValueError('All securities, futures and crypto source controls must pass before generation writing')
    record=build_candidate(service,session,draft)
    if record.to_dict()['source_lineage']!=checked['source_lineage']:
        raise ValueError('Futures calculation and monthly source partition differ')
    crypto_candidate=None
    from campaigniq.account_crypto_statement_sources import load_receipt
    if load_receipt(session,draft) is not None:
        from campaigniq.account_crypto_generation import build_candidate as build_crypto
        if not crypto_applicable:raise ValueError('Approved crypto cannot be omitted')
        crypto_candidate=build_crypto(service,session,draft)
    target=session.artifacts;buffer=GenerationBuffer(target)
    prefix=record.period_start.strftime('%Y-%m')+'-'
    try:
        session.artifacts=buffer
        result=_publish_in_session(service,session,actor_id,account_id,draft_id,revision,
            forex_applicable=forex_applicable,crypto_applicable=crypto_applicable,
            receipt_format='campaigniq.deployment_account_publication',_futures_projection=checked['futures'],_crypto_candidate=crypto_candidate)
        # Crypto record source row IDs must refer to the complete original export,
        # never the reindexed temporary securities projection.
        if crypto_applicable and crypto_candidate is None:
            from campaigniq.importers.thinkorswim.crypto_reader import read_crypto_report
            from campaigniq.persistence.crypto_month import build_crypto_month,load_preceding_crypto,serialize_crypto_month
            from campaigniq.importers.schwab.crypto_statement_reader import read_crypto_statement,attach_crypto_statement
            from campaigniq.import_contract import MonthlyInputRole
            from campaigniq.import_draft_bridge import restore_uploads
            from campaigniq.monthly_upload_staging import stage_monthly_uploads
            uploads=restore_uploads(session.drafts,draft.id)
            with TemporaryDirectory(prefix='campaigniq-original-crypto-generation-') as tmp:
                supplied=stage_monthly_uploads(upload_dir=Path(tmp),uploads=uploads)
                report=read_crypto_report(supplied[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY],
                    period_start=record.period_start,period_end=record.period_end)
                crypto=build_crypto_month(report,load_preceding_crypto(buffer,record.period_start)) if report is not None else None
                statement=supplied.get(MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT)
                if statement is not None:
                    crypto=attach_crypto_statement(crypto,read_crypto_statement(statement,
                        period_start=record.period_start,period_end=record.period_end))
                if crypto is not None:buffer.write_text(prefix+'crypto.json',serialize_crypto_month(crypto))
        buffer.write_text(prefix+'futures.json',record.record_json)
        receipt_key=prefix+'account-publication-receipt.json'
        receipt=json.loads(buffer.read_text(receipt_key));receipt['futures_generation']=reference(record)
        if crypto_candidate is not None:
            from campaigniq.account_crypto_publication import reference as crypto_reference
            receipt['approved_crypto_generation']=crypto_reference(crypto_candidate,buffer.read_text(prefix+'crypto.json'))
        text=json.dumps(receipt,sort_keys=True,indent=2)+'\n';buffer.write_text(receipt_key,text)
        # Verify the whole candidate through the actual account result and analytics
        # loaders before sending any artifact INSERT to the restricted writer.
        from campaigniq.account_deployment_results import _deployment_results_session
        from campaigniq.dashboard_sources import load_dashboard_sources
        loaded=_deployment_results_session(service,session,draft,account_id,revision)
        summaries,_,_=load_dashboard_sources(buffer)
        month=next(s for s in summaries if s.period_end==record.period_end)
        if not loaded.futures_applicable or month.futures_realized_pnl!=record.net_realized_usd:
            raise ValueError('Complete generation report parity failed')
        if build_candidate(service,session,draft)!=record:raise ValueError('Futures source changed during generation')
        session._check()
        keys=tuple(sorted(buffer.rows))
    finally:session.artifacts=target
    # Finalized marker is the last INSERT, including after the extended receipt.
    marker=prefix+'finalized.json'
    if marker not in keys:raise ValueError('Generation visibility marker missing')
    for key in (*[k for k in keys if k!=marker],marker):target.write_text(key,buffer.rows[key])
    verify_published(service,session,draft)
    if crypto_candidate is not None:
        from campaigniq.account_crypto_publication import verify_published as verify_crypto
        verify_crypto(service,session,draft)
    session._check()
    return replace(result,artifact_keys=target.list_keys(prefix=prefix),receipt_sha256=sha256(text.encode()).hexdigest())
