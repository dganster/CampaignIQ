"""Actual restricted input-role crypto receipts inside the fixed disposable clone."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier,Event
from unittest.mock import patch
from decimal import Decimal
import json

from campaigniq import account_crypto_statement_sources as crypto
from campaigniq.account_administration import Permission
from campaigniq.crypto_account_rehearsal_sources import sources
from campaigniq.persistence.account_data_session import _Drafts


def verify_crypto_sources(c,passed):
    service,user,reader=c.inputs,c.user,c.reader
    owner=reader.snapshot(user).user(c.identities[0]).id
    reader.execute(user,reader.snapshot(user).revision,'add_account',owner_id=owner,name='Synthetic crypto source review',broker='schwab')
    account=next(a.id for a in reader.snapshot(user).accounts if a.name=='Synthetic crypto source review')
    service.gateway.bind(user,account,'securities','97531864',expected_revision=reader.snapshot(user).revision)
    uploads,previous,current=sources();draft=service.create(user,account,2026,10);revision=0
    for role,upload in uploads.items():revision=service.save(user,account,draft.id,revision,role,upload)
    before=c.snapshot()
    review=service.review_crypto_statements(user,account,draft.id,revision,previous,current)
    assert c.snapshot()==before and not review['publication_authorized']
    reviewed=review['reviewed_preview_sha256']
    def save(actor=user,rev=revision,digest=reviewed,**extra):
        return service.save_crypto_statement_sources(actor,account,draft.id,rev,previous,current,
            reviewed_preview_sha256=digest,confirm_same_crypto_account=True,confirm_source_choices=True,**extra)
    def rejected(kind,call):
        try:call()
        except kind:return
        raise AssertionError('Expected rejection')
    rejected(PermissionError,lambda:save(c.foreign))
    rejected(ValueError,lambda:save(rev=0))
    rejected(ValueError,lambda:save(digest='0'*64))
    assert c.snapshot()==before
    passed('crypto_source_review_uses_readonly_snapshot_and_rejects_foreign_stale_and_changed_review_without_writes')
    real=_Drafts.upload
    for role in (crypto.PREVIOUS,crypto.CURRENT,crypto.RECEIPT):
        def fail(repository,draft_id,kind,*args,**kwargs):
            result=real(repository,draft_id,kind,*args,**kwargs)
            if kind==role:raise RuntimeError('injected source save failure')
            return result
        with patch.object(_Drafts,'upload',new=fail):rejected(RuntimeError,save)
        assert c.snapshot()==before
    passed('failure_after_either_crypto_pdf_or_source_receipt_rolls_back_all_document_versions_and_revision')
    def caught(repository,draft_id,kind,*args,**kwargs):
        result=real(repository,draft_id,kind,*args,**kwargs)
        if kind==crypto.CURRENT:
            import psycopg
            try:
                with repository._session._operation() as conn:conn.execute('UPDATE public.artifact_storage_dev SET content=content WHERE false')
            except psycopg.Error:pass
        return result
    with patch.object(_Drafts,'upload',new=caught):rejected(RuntimeError,save)
    assert c.snapshot()==before
    passed('caught_forbidden_sql_during_crypto_source_save_cannot_commit_partial_documents')
    barrier=Barrier(3)
    def attempt():
        barrier.wait(timeout=15)
        try:return 'winner',save()
        except ValueError:return 'blocked',None
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(attempt) for _ in range(2)];barrier.wait(timeout=15)
        results=[j.result(timeout=30) for j in jobs]
    assert sorted(r[0] for r in results)==['blocked','winner']
    saved_revision=next(r[1] for r in results if r[0]=='winner')
    assert saved_revision==revision+3
    after=c.snapshot();assert save(rev=saved_revision)==saved_revision and c.snapshot()==after
    passed('independent_concurrent_crypto_source_approval_has_one_winner_and_exact_repeat_changes_nothing')
    result=service.crypto_statement_preview(user,account,draft.id,saved_revision)
    assert result['source_receipt_verified'] and not result['publication_authorized']
    assert Decimal(result['calculation']['totals']['full_ending_cost_basis_usd'])==500
    with service.gateway.transaction(user,account) as session:
        assert session.drafts.document(draft.id,crypto.PREVIOUS)[2]==previous.content
        assert session.drafts.document(draft.id,crypto.CURRENT)[2]==current.content
        receipt=crypto.load_receipt(session,session.drafts.get(draft.id))
        assert receipt['confirmed_by']==owner and receipt['broker_verified_account_link'] is False
    assert c.snapshot()==after
    passed('independent_reader_rebuilds_crypto_calculation_from_exact_saved_pdfs_and_owner_attributed_receipt_without_writes')
    from campaigniq.account_crypto_generation import verify_candidate
    candidate=service.crypto_generation_preview(user,account,draft.id,saved_revision)
    assert candidate==service.crypto_generation_preview(user,account,draft.id,saved_revision)
    assert candidate['candidate_only'] and candidate['publication_authorized'] is False
    assert Decimal(candidate['artifact']['calculation']['totals']['full_ending_cost_basis_usd'])==500
    with service.gateway.transaction(user,account) as session:
        record=verify_candidate(service,session,session.drafts.get(draft.id),candidate['artifact_text'])
        assert record.sha256==candidate['artifact_sha256']
        forged=json.loads(record.record_json);forged['source_receipt_sha256']='0'*64
        rejected(ValueError,lambda:verify_candidate(service,session,session.drafts.get(draft.id),json.dumps(forged)))
    rejected(ValueError,lambda:service.crypto_generation_preview(user,account,draft.id,saved_revision-1))
    rejected(PermissionError,lambda:service.crypto_generation_preview(c.foreign,account,draft.id,saved_revision))
    assert c.snapshot()==after
    passed('crypto_financial_candidate_rebuilds_exact_approved_sources_and_rejects_forged_foreign_stale_results_without_writes')
    foreign=reader.snapshot(user).user(c.identities[1]).id
    def command(name,**kwargs):return reader.execute(user,reader.snapshot(user).revision,name,**kwargs)
    command('set_grant',user_id=foreign,account_id=account,permission=Permission.EDIT)
    assert service.crypto_statement_preview(c.foreign,account,draft.id,saved_revision)['source_receipt_verified']
    rejected(PermissionError,lambda:save(c.foreign,rev=saved_revision))
    command('configure_account',account_id=account,archived=True)
    assert service.crypto_statement_preview(c.foreign,account,draft.id,saved_revision)['source_receipt_verified']
    rejected(PermissionError,lambda:save(rev=saved_revision))
    command('configure_account',account_id=account,archived=False)
    command('configure_user',user_id=foreign,enabled=False)
    rejected(PermissionError,lambda:service.crypto_statement_preview(c.foreign,account,draft.id,saved_revision))
    command('configure_user',user_id=foreign,enabled=True)
    command('set_grant',user_id=foreign,account_id=account,permission=None)
    rejected(PermissionError,lambda:service.crypto_statement_preview(c.foreign,account,draft.id,saved_revision))
    passed('crypto_grants_revocation_archive_and_disabled_access_are_rechecked_and_only_owner_can_approve_sources')
    original_evaluate=crypto.evaluate
    def readonly_sql(service,session,*args):
        with session._operation() as conn:conn.execute('UPDATE campaigniq_accounts.import_drafts SET revision=revision WHERE false')
        return original_evaluate(service,session,*args)
    import psycopg
    with patch.object(crypto,'evaluate',new=readonly_sql):
        rejected(psycopg.errors.ReadOnlySqlTransaction,lambda:service.crypto_statement_preview(user,account,draft.id,saved_revision))
        rejected(psycopg.errors.ReadOnlySqlTransaction,lambda:service.crypto_generation_preview(user,account,draft.id,saved_revision))
    passed('crypto_preview_database_transaction_rejects_raw_sql_writes')
    with c.owner() as conn:
        row=conn.execute('SELECT id,sha256,content FROM campaigniq_accounts.import_draft_documents WHERE draft_id=%s AND role=%s',
            (draft.id,crypto.CURRENT)).fetchone()
        from hashlib import sha256
        changed=bytes(row[2])+b'\n'
        conn.execute('UPDATE campaigniq_accounts.import_draft_documents SET sha256=%s,content=%s WHERE id=%s',
            (sha256(changed).hexdigest(),changed,row[0]))
    try:
        rejected(ValueError,lambda:service.crypto_statement_preview(user,account,draft.id,saved_revision))
        rejected(ValueError,lambda:service.crypto_generation_preview(user,account,draft.id,saved_revision))
    finally:
        with c.owner() as conn:conn.execute('UPDATE campaigniq_accounts.import_draft_documents SET sha256=%s,content=%s WHERE id=%s',(row[1],row[2],row[0]))
    assert c.snapshot()['rows']['artifact_storage_dev']==before['rows']['artifact_storage_dev']
    passed('changed_crypto_original_withholds_preview_without_repair_and_all_existing_financial_artifacts_are_unchanged')

    verify_crypto_publication(c,passed,account,draft,saved_revision)
    verify_combined_publication(c,passed)


def verify_crypto_publication(c,passed,account,draft,revision,*,combined=False):
    from campaigniq.account_crypto_publication import _publish_crypto_in_session
    from campaigniq.account_deployment_publication import AppendOnlyArtifacts
    from campaigniq.account_deployment_results import load_deployment_results
    from campaigniq.account_deployment_dashboard import DeploymentAccountDashboard
    from campaigniq.persistence.crypto_month import load_preceding_crypto
    from datetime import date
    service,user=c.inputs,c.user
    if combined:
        from campaigniq.account_futures_publication import _publish_futures_in_session
        writer=_publish_futures_in_session
    else:writer=_publish_crypto_in_session
    def publish():
        with c.publisher._transaction(user,account) as (session,actor):
            return writer(service,session,actor,account,draft.id,revision,
                forex_applicable=False,crypto_applicable=True)
    def reject(kind,call):
        try:call()
        except kind:return
        raise AssertionError('Expected rejection '+kind.__name__)
    before=c.snapshot();real=AppendOnlyArtifacts.write_text
    for suffix in ('crypto.json','account-publication-receipt.json','finalized.json'):
        def fail(storage,key,text):
            real(storage,key,text)
            if key.endswith(suffix):raise RuntimeError('injected crypto generation failure')
        with patch.object(AppendOnlyArtifacts,'write_text',new=fail):reject(RuntimeError,publish)
        assert c.snapshot()==before
    passed('restricted_crypto_writer_rolls_back_complete_month_after_component_receipt_and_marker_failures')
    def caught(storage,key,text):
        real(storage,key,text)
        if key.endswith('crypto.json'):
            import psycopg
            try:
                with storage._session._operation() as conn:conn.execute('UPDATE public.artifact_storage_dev SET content=content WHERE false')
            except psycopg.Error:pass
    with patch.object(AppendOnlyArtifacts,'write_text',new=caught):reject(RuntimeError,publish)
    assert c.snapshot()==before
    passed('caught_forbidden_sql_cannot_commit_partial_crypto_generation')
    barrier=Barrier(3);marker=Event();release=Event()
    def paused(storage,key,text):
        real(storage,key,text)
        if key.endswith('finalized.json'):
            marker.set()
            if not release.wait(20):raise RuntimeError('Crypto reader coordination timed out')
    def attempt():
        barrier.wait(timeout=15)
        try:publish();return 'winner'
        except ValueError:return 'blocked'
    with patch.object(AppendOnlyArtifacts,'write_text',new=paused),ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(attempt) for _ in range(2)]
        try:
            barrier.wait(timeout=15)
            if not marker.wait(20):raise RuntimeError('Crypto marker not reached')
            reject(ValueError,lambda:load_deployment_results(service,user,account,draft.id,revision))
        finally:release.set()
        assert sorted(j.result(timeout=30) for j in jobs)==['blocked','winner']
    after=c.snapshot();out=load_deployment_results(service,user,account,draft.id,revision)
    if combined:assert out.futures_applicable and Decimal(out.futures_realized_usd)==-816
    assert sum(Decimal(q) for _,q in out.positions)==110
    assert Decimal(dict(out.crypto_positions)['BTC/USD'])==Decimal('.005929')
    snapshot=DeploymentAccountDashboard(c.reader).snapshot(user,account,include_history=True)
    assert snapshot.history and not snapshot.history.gaps
    with service.gateway.transaction(user,account) as session:
        crypto_next=load_preceding_crypto(session.artifacts,date(2026,11,1))
        assert sum(Decimal(l['cost_usd']) for l in crypto_next['ending_lots']['BTC/USD'])==500
    assert c.snapshot()==after
    passed('committed_crypto_has_one_winner_and_independent_results_history_and_following_opening_preserve_exact_lots')
    scope=c.reader.authorize(user,account).storage_scope
    with c.owner() as conn:
        original=conn.execute('SELECT content FROM campaigniq_accounts.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s',
            (scope,'2026-10-crypto.json')).fetchone()[0]
        changed=json.loads(original);changed['ending_quantities']['BTC/USD']='999'
        conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',
            (json.dumps(changed),scope,'2026-10-crypto.json'))
    try:
        reject(ValueError,lambda:load_deployment_results(service,user,account,draft.id,revision))
        reject(ValueError,lambda:DeploymentAccountDashboard(c.reader).snapshot(user,account))
    finally:
        with c.owner() as conn:conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',
            (original,scope,'2026-10-crypto.json'))
    assert c.snapshot()==after
    passed('committed_crypto_conflict_withholds_entire_account_result_and_dashboard_without_repair')


def verify_combined_publication(c,passed):
    import csv,io
    from campaigniq.futures_rehearsal_sources import FILLS,LEDGER,CASH_HEADER,statement_pdf
    from campaigniq.import_draft_bridge import RestoredUpload
    reader,service,user=c.reader,c.inputs,c.user
    owner=reader.snapshot(user).user(c.identities[0]).id
    reader.execute(user,reader.snapshot(user).revision,'add_account',owner_id=owner,name='Synthetic combined monthly generation',broker='schwab')
    account=next(a.id for a in reader.snapshot(user).accounts if a.name=='Synthetic combined monthly generation')
    service.gateway.bind(user,account,'securities','97531865',expected_revision=reader.snapshot(user).revision)
    uploads,previous,current=sources('97531865')
    history=uploads['thinkorswim_trade_history']
    def rows(items):
        out=io.StringIO();csv.writer(out,lineterminator='\n').writerows(items);return out.getvalue().encode()
    raw=history.content.replace(b',10/15/26 10:00:00,STOCK',rows(FILLS)+b',10/15/26 10:00:00,STOCK')
    raw=raw.replace(b'Forex Statements\n',b'Futures Statements\n'+rows([CASH_HEADER,*LEDGER])+b'\nForex Statements\n')
    uploads['thinkorswim_trade_history']=RestoredUpload(history.name,history.media_type,raw)
    draft=service.create(user,account,2026,10);revision=0
    for role,upload in uploads.items():revision=service.save(user,account,draft.id,revision,role,upload)
    revision=service.confirm_empty_futures_opening(user,account,draft.id,revision,confirm_empty_opening=True)
    revision=service.save_futures_statement(user,account,draft.id,revision,RestoredUpload('future.pdf','application/pdf',statement_pdf()),confirm_same_account=True)
    revision=service.confirm_empty_futures_closing(user,account,draft.id,revision,confirm_empty_closing=True)
    review=service.review_crypto_statements(user,account,draft.id,revision,previous,current)
    revision=service.save_crypto_statement_sources(user,account,draft.id,revision,previous,current,
        reviewed_preview_sha256=review['reviewed_preview_sha256'],confirm_same_crypto_account=True,confirm_source_choices=True)
    verify_crypto_publication(c,lambda name:passed('combined_futures_'+name),account,draft,revision,combined=True)
