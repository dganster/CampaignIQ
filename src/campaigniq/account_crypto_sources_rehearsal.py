"""Actual restricted input-role crypto receipts inside the fixed disposable clone."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
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
