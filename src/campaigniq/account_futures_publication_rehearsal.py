"""Synthetic futures checks inside the existing identity-checked disposable clone."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier,Event
from unittest.mock import patch
import json

from campaigniq.account_futures_publication import _publish_futures_in_session
from campaigniq.account_deployment_publication import AppendOnlyArtifacts
from campaigniq.account_deployment_results import load_deployment_results
from campaigniq.account_deployment_dashboard import DeploymentAccountDashboard
from campaigniq.dashboard_sources import load_dashboard_sources
from campaigniq.futures_rehearsal_sources import sources,statement_pdf
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.authoritative_lot_state import load_preceding_authoritative_state_from_storage


def verify_futures(c,passed):
    reader,service,publisher,user=c.reader,c.inputs,c.publisher,c.user
    owner_id=reader.snapshot(user).user(c.identities[0]).id
    reader.execute(user,reader.snapshot(user).revision,'add_account',owner_id=owner_id,name='Synthetic futures rehearsal',broker='schwab')
    account=next(a.id for a in reader.snapshot(user).accounts if a.name=='Synthetic futures rehearsal')
    service.gateway.bind(user,account,'securities','24681357',expected_revision=reader.snapshot(user).revision)
    draft=service.create(user,account,2026,10);revision=0
    for role,upload in sources('24681357').items():revision=service.save(user,account,draft.id,revision,role,upload)
    revision=service.confirm_empty_futures_opening(user,account,draft.id,revision,confirm_empty_opening=True)
    revision=service.save_futures_statement(user,account,draft.id,revision,RestoredUpload('synthetic.pdf','application/pdf',statement_pdf()),confirm_same_account=True)
    revision=service.confirm_empty_futures_closing(user,account,draft.id,revision,confirm_empty_closing=True)
    initial=c.snapshot()
    def publish(actor=user,rev=revision):
        with publisher._transaction(actor,account) as (session,actor_id):
            return _publish_futures_in_session(service,session,actor_id,account,draft.id,rev,
                forex_applicable=False,crypto_applicable=False)
    def rejected(kind,call):
        try:call()
        except kind:return
        raise AssertionError('Expected rejection '+kind.__name__)
    rejected(PermissionError,lambda:publisher.publish(user,account,draft.id,revision,confirm_publication=True,forex_applicable=False,crypto_applicable=False))
    rejected(PermissionError,lambda:publish(c.foreign))
    rejected(ValueError,lambda:publish(rev=0))
    assert c.snapshot()==initial
    passed('futures_public_entry_stays_disabled_and_current_owner_and_revision_are_required_without_writes')
    real=AppendOnlyArtifacts.write_text
    for suffix in ('futures.json','account-publication-receipt.json','finalized.json'):
        def fail(storage,key,text):
            real(storage,key,text)
            if key.endswith(suffix):raise RuntimeError('injected futures generation failure')
        with patch.object(AppendOnlyArtifacts,'write_text',new=fail):rejected(RuntimeError,publish)
        assert c.snapshot()==initial
    passed('restricted_futures_writer_rolls_back_all_generation_rows_after_component_receipt_and_marker_failures')
    def caught_failure(storage,key,text):
        real(storage,key,text)
        if key=='2026-10-futures.json':
            import psycopg
            try:
                with storage._session._operation() as conn:
                    conn.execute('UPDATE public.artifact_storage_dev SET content=content WHERE false')
            except psycopg.Error:pass
    with patch.object(AppendOnlyArtifacts,'write_text',new=caught_failure):rejected(RuntimeError,publish)
    assert c.snapshot()==initial
    passed('caught_forbidden_sql_after_futures_insert_cannot_commit_a_partial_generation')
    barrier=Barrier(3);marker=Event();release=Event()
    def paused(storage,key,text):
        real(storage,key,text)
        if key=='2026-10-finalized.json':
            marker.set()
            if not release.wait(20):raise RuntimeError('Futures reader coordination timed out')
    def attempt():
        barrier.wait(timeout=15)
        try:publish();return 'winner'
        except ValueError:return 'blocked'
    with patch.object(AppendOnlyArtifacts,'write_text',new=paused),ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(attempt) for _ in range(2)]
        try:
            barrier.wait(timeout=15)
            if not marker.wait(20):raise RuntimeError('Futures generation never reached final marker')
            rejected(ValueError,lambda:load_deployment_results(service,user,account,draft.id,revision))
        finally:release.set()
        assert sorted(j.result(timeout=30) for j in jobs)==['blocked','winner']
    passed('independent_reader_cannot_see_uncommitted_futures_and_simultaneous_publications_have_one_winner')
    after=c.snapshot()
    out=load_deployment_results(service,user,account,draft.id,revision)
    assert out.futures_applicable and Decimal(out.futures_realized_usd)==-816
    assert sum(Decimal(q) for _,q in out.positions)==110
    snapshot=DeploymentAccountDashboard(reader).snapshot(user,account,include_history=True)
    summaries,_,_=load_dashboard_sources(snapshot)
    assert summaries[-1].combined_realized_pnl==-816 and not snapshot.history.gaps
    assert len(snapshot.history.originals)==1
    with service.gateway.transaction(user,account) as session:
        book=load_preceding_authoritative_state_from_storage(session.artifacts,period_start=date(2026,11,1))
        assert sum(l.quantity for rows in book[2]._lots.values() for l in rows)==110
        originals=json.loads(session.artifacts.read_text('2026-10-import-provenance.json'))
        from hashlib import sha256
        raw=session.drafts.document(draft.id,'thinkorswim_trade_history')[2]
        assert next(r for r in originals['inputs'] if r['role']=='thinkorswim_trade_history')['sha256']==sha256(raw).hexdigest()
    assert c.snapshot()==after
    passed('independent_canonical_results_dashboard_history_provenance_and_following_opening_match_complete_futures_generation')
    scope=reader.authorize(user,account).storage_scope
    with c.owner() as conn:
        original=conn.execute('SELECT content FROM campaigniq_accounts.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s',
            (scope,'2026-10-futures.json')).fetchone()[0]
        body=json.loads(original);body['financials']['fees_cash_usd']='-15.00'
        conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',
            (json.dumps(body),scope,'2026-10-futures.json'))
    try:
        rejected(ValueError,lambda:load_deployment_results(service,user,account,draft.id,revision))
        rejected(ValueError,lambda:DeploymentAccountDashboard(reader).snapshot(user,account))
    finally:
        with c.owner() as conn:conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',
            (original,scope,'2026-10-futures.json'))
    assert c.snapshot()==after
    passed('committed_futures_conflict_withholds_whole_result_and_dashboard_without_reader_repair')
