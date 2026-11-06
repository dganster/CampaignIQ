"""Real restricted-role statement-only publication in the existing disposable clone."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from threading import Barrier,Event
from unittest.mock import patch

from campaigniq.account_administration import Permission
from campaigniq.account_deployment_application import DeploymentMonthlyApplication
from campaigniq.account_deployment_publication import AppendOnlyArtifacts
from campaigniq.account_deployment_results import load_deployment_results
from campaigniq.account_forex_cash_sources_rehearsal import rejected
from campaigniq.no_trading_rehearsal_sources import sources
from campaigniq.persistence.authoritative_lot_state import load_preceding_authoritative_state_from_storage
from campaigniq.persistence.account_data_session import _Drafts
from campaigniq import account_no_trading as mode


def verify_no_trading(c,passed):
    service,user,reader=c.inputs,c.user,c.reader
    owner=reader.snapshot(user).user(c.identities[0]).id
    name='Synthetic no trading statement month'
    reader.execute(user,reader.snapshot(user).revision,'add_account',owner_id=owner,name=name,broker='schwab')
    account=next(a.id for a in reader.snapshot(user).accounts if a.name==name)
    service.gateway.bind(user,account,'securities','24681360',expected_revision=reader.snapshot(user).revision)
    draft=service.create(user,account,2026,10);upload,fixtures=sources()
    revision=service.save(user,account,draft.id,0,'schwab_brokerage_statement',upload)
    scope=reader.authorize(user,account).storage_scope
    # Only synthetic clone fixture rows; no retained account is seeded or edited.
    with c.owner() as conn:
        for key,text in fixtures.items():
            conn.execute('INSERT INTO campaigniq_accounts.artifact_storage_dev(namespace,artifact_key,content) VALUES(%s,%s,%s)',(scope,key,text))
    before=c.snapshot();review=service.no_trading_preview(user,account,draft.id,revision)
    assert review['review']['financials']['income_usd']=='59.00' and not review['owner_confirmed']
    assert c.snapshot()==before
    def confirm(actor=user,rev=revision,digest=review['review_sha256']):
        return service.save_no_trading_confirmation(actor,account,draft.id,rev,reviewed_sha256=digest,confirm_no_trading=True,confirm_cash_income=True)
    rejected(PermissionError,lambda:confirm(c.foreign))
    rejected(ValueError,lambda:confirm(rev=revision-1))
    rejected(ValueError,lambda:confirm(digest='0'*64))
    real_upload=_Drafts.upload
    def failed(repository,did,role,*args,**kwargs):
        value=real_upload(repository,did,role,*args,**kwargs)
        if role==mode.ROLE:raise RuntimeError('Injected owner confirmation failure')
        return value
    with patch.object(_Drafts,'upload',new=failed):rejected(RuntimeError,confirm)
    assert c.snapshot()==before
    revision=confirm()
    assert confirm(rev=revision)==revision
    passed('no_trading_statement_review_and_owner_receipt_recheck_scope_revision_and_rollback_without_financial_writes')
    old_enabled=c.publisher.multi_asset_enabled;c.publisher.multi_asset_enabled=True
    try:
        app=DeploymentMonthlyApplication(service,c.publisher,publication_enabled=True)
        def publish(actor=user,rev=revision):
            return app.publish(actor,account,draft.id,rev,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
        before=c.snapshot();real=AppendOnlyArtifacts.write_text
        for suffix in ('lot-book.json','position-journal.json','realized-attributions.json','forex-settlement-attributions.json',
                'lifecycle-transitions.json','boundary-completeness.json','market-applicability.json','import-provenance.json',
                'no-trading-month.json','account-publication-receipt.json','finalized.json'):
            reached=[]
            def fail(storage,key,text):
                real(storage,key,text)
                if key=='2026-10-'+suffix:reached.append(key);raise RuntimeError('Injected statement-only generation failure')
            with patch.object(AppendOnlyArtifacts,'write_text',new=fail):rejected(RuntimeError,publish)
            assert reached and c.snapshot()==before
        passed('no_trading_failure_after_each_canonical_component_income_record_receipt_and_marker_rolls_back_every_generation_row')
        def caught(storage,key,text):
            real(storage,key,text)
            if key=='2026-10-no-trading-month.json':
                import psycopg
                try:
                    with storage._session._operation() as conn:conn.execute('UPDATE public.artifact_storage_dev SET content=content WHERE false')
                except psycopg.Error:pass
                else:raise AssertionError('Publication role unexpectedly permits retained data updates')
        with patch.object(AppendOnlyArtifacts,'write_text',new=caught):rejected(RuntimeError,publish)
        assert c.snapshot()==before
        passed('no_trading_caught_forbidden_sql_cannot_commit_partial_generation')
        barrier,ready,release=Barrier(3),Event(),Event()
        def paused(storage,key,text):
            real(storage,key,text)
            if key=='2026-10-finalized.json':
                ready.set()
                if not release.wait(20):raise RuntimeError('No-trading publication coordination timeout')
        def attempt():
            barrier.wait(timeout=15)
            try:publish();return 'winner'
            except ValueError:return 'blocked'
        with patch.object(AppendOnlyArtifacts,'write_text',new=paused),ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(attempt) for _ in range(2)]
            try:
                barrier.wait(timeout=15)
                if not ready.wait(20):raise RuntimeError('No-trading marker never reached')
                with service.gateway.transaction(user,account) as old:
                    assert not old.artifacts.list_keys(prefix='2026-10-')
                    release.set();assert sorted(j.result(timeout=45) for j in jobs)==['blocked','winner']
                    assert not old.artifacts.list_keys(prefix='2026-10-')
            finally:release.set()
        passed('no_trading_simultaneous_publications_have_one_winner_and_repeatable_reader_never_sees_partial_month')
        after=c.snapshot()
        def read(actor=user):return load_deployment_results(service,actor,account,draft.id,revision)
        result=read()
        assert result.no_trading_month and Decimal(result.cash_income_usd)==59 and Decimal(result.brokerage_realized_usd)==0
        with service.gateway.transaction(user,account) as session:
            from campaigniq.persistence.lot_book_store import deserialize_lot_book
            original=deserialize_lot_book(fixtures['2026-09-lot-book.json']).lot_book
            opening=load_preceding_authoritative_state_from_storage(session.artifacts,period_start=date(2026,11,1))
            assert opening[0]==date(2026,10,31) and opening[2]._lots==original._lots and opening[2]._next_id==original._next_id
            assert all(session.artifacts.read_text(k)==v for k,v in fixtures.items())
        assert c.snapshot()==after
        assert after['rows']['import_draft_documents']==before['rows']['import_draft_documents']
        passed('no_trading_independent_reader_verifies_dividend_separate_from_zero_trading_and_november_opening_preserves_exact_lots_and_campaigns')
        foreign=reader.snapshot(user).user(c.identities[1]).id
        reader.execute(user,reader.snapshot(user).revision,'set_grant',user_id=foreign,account_id=account,permission=Permission.VIEW)
        assert read(c.foreign)==result
        reader.execute(user,reader.snapshot(user).revision,'set_grant',user_id=foreign,account_id=account,permission=Permission.EDIT)
        rejected(PermissionError,lambda:confirm(c.foreign,rev=revision))
        reader.execute(user,reader.snapshot(user).revision,'set_grant',user_id=foreign,account_id=account,permission=None)
        rejected(PermissionError,lambda:read(c.foreign))
        passed('no_trading_owner_confirmation_and_committed_reader_recheck_view_edit_and_revoked_access')
        import psycopg
        from campaigniq import account_no_trading_publication as publication
        real_verify=publication.verify
        def forbidden(service,session,*args):
            with session._operation() as conn:conn.execute('UPDATE campaigniq_accounts.import_drafts SET revision=revision WHERE false')
            return real_verify(service,session,*args)
        snapshot=c.snapshot()
        with patch.object(publication,'verify',new=forbidden):rejected(psycopg.errors.ReadOnlySqlTransaction,read)
        assert c.snapshot()==snapshot
        passed('no_trading_independent_result_reader_database_rejects_raw_sql_writes')
        keys=['2026-10-position-journal.json','2026-10-no-trading-month.json','2026-10-account-publication-receipt.json']
        with c.owner() as conn:
            original={k:conn.execute('SELECT content FROM campaigniq_accounts.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s',(scope,k)).fetchone()[0] for k in keys}
            journal=json.loads(original[keys[0]]);journal['entries'][0]['lots'][0]['lot_id']='forged'
            text=json.dumps(journal,sort_keys=True,indent=2)+'\n'
            record=json.loads(original[keys[1]]);record['canonical_artifact_sha256'][keys[0]]=sha256(text.encode()).hexdigest()
            rtext=json.dumps(record,sort_keys=True,indent=2)+'\n'
            receipt=json.loads(original[keys[2]]);receipt['no_trading_generation']['sha256']=sha256(rtext.encode()).hexdigest()
            for k,v in zip(keys,(text,rtext,json.dumps(receipt))):conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',(v,scope,k))
        try:
            corrupt=c.snapshot();rejected(ValueError,read);assert c.snapshot()==corrupt
        finally:
            with c.owner() as conn:
                for k,v in original.items():conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',(v,scope,k))
        assert read()==result
        passed('no_trading_coherently_rehashed_journal_is_withheld_without_reader_repair')
    finally:c.publisher.multi_asset_enabled=old_enabled
