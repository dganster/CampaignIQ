"""Actual approved Forex publication checks, only inside the disposable clone."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from threading import Barrier, Event
from unittest.mock import patch

from campaigniq.account_administration import Permission
from campaigniq.account_deployment_application import DeploymentMonthlyApplication
from campaigniq.account_deployment_publication import AppendOnlyArtifacts
from campaigniq.account_deployment_results import load_deployment_results
from campaigniq.account_forex_cash_sources_rehearsal import rejected
from campaigniq.forex_publication_rehearsal_sources import sources
from campaigniq.forex_inventory_rehearsal_sources import predecessor
from campaigniq.persistence.monthly_publication import (
    PROTOCOL_FILE, _serialize_publication_protocol, _serialize_finalized_month_marker)


def _setup(c, combined):
    service, user, reader = c.inputs, c.user, c.reader
    name = 'Synthetic approved Forex publication' + (' combined' if combined else '')
    owner = reader.snapshot(user).user(c.identities[0]).id
    reader.execute(user, reader.snapshot(user).revision, 'add_account', owner_id=owner, name=name, broker='schwab')
    account = next(a.id for a in reader.snapshot(user).accounts if a.name == name)
    securities, forex = ('24681359', '8642099') if combined else ('24681358', '8642098')
    for kind, number in (('securities', securities), ('forex', forex)):
        service.gateway.bind(user, account, kind, number, expected_revision=reader.snapshot(user).revision)
    uploads, cash, supplement, daily, previous, current, future = sources(securities, forex, combined=combined)
    draft = service.create(user, account, 2026, 10)
    revision = 0
    for role, upload in uploads.items():
        revision = service.save(user, account, draft.id, revision, role, upload)
    scope = reader.authorize(user, account).storage_scope
    # Synthetic clone-only predecessor fixtures. No production or retained row is seeded.
    fixtures = {PROTOCOL_FILE: _serialize_publication_protocol(first_period_end=date(2026, 9, 30)),
        '2026-09-lot-book.json': predecessor(),
        '2026-09-finalized.json': _serialize_finalized_month_marker(period_end=date(2026, 9, 30))}
    with c.owner() as conn:
        for key, text in fixtures.items():
            conn.execute('INSERT INTO campaigniq_accounts.artifact_storage_dev(namespace,artifact_key,content) VALUES(%s,%s,%s)', (scope,key,text))
    if combined:
        revision = service.confirm_empty_futures_opening(user, account, draft.id, revision, confirm_empty_opening=True)
        revision = service.save_futures_statement(user, account, draft.id, revision, future, confirm_same_account=True)
        revision = service.confirm_empty_futures_closing(user, account, draft.id, revision, confirm_empty_closing=True)
        review = service.review_crypto_statements(user, account, draft.id, revision, previous, current)
        revision = service.save_crypto_statement_sources(user, account, draft.id, revision, previous, current,
            reviewed_preview_sha256=review['reviewed_preview_sha256'],confirm_same_crypto_account=True,confirm_source_choices=True)
    review = service.review_forex_cash_sources(user, account, draft.id, revision, cash, supplement)
    revision = service.save_forex_cash_sources(user, account, draft.id, revision, cash, supplement,
        reviewed_preview_sha256=review['review_sha256'],confirm_same_account=True,confirm_source_choices=True)
    review = service.forex_daily_inventory_preview(user, account, draft.id, revision, daily)
    revision = service.save_forex_daily_sources(user, account, draft.id, revision, daily,
        reviewed_preview_sha256=review['review_sha256'],confirm_same_account=True,confirm_coverage_limits=True)
    review = service.forex_financial_candidate_preview(user, account, draft.id, revision)
    revision = service.save_forex_financial_policy(user, account, draft.id, revision,
        reviewed_candidate_sha256=review['artifact_sha256'],confirm_daily_basis=True,
        confirm_retained_differences=True,confirm_coverage_limits=True)
    return account, draft, revision, scope


def verify_forex_publication(c, passed):
    enabled = c.publisher.multi_asset_enabled
    c.publisher.multi_asset_enabled = True  # Disposable process only; restored below.
    try:
        for combined in (False, True):
            _verify(c, passed, combined)
    finally:
        c.publisher.multi_asset_enabled = enabled


def _verify(c, passed, combined):
    from campaigniq import account_forex_publication as publication
    from campaigniq.dashboard_sources import load_dashboard_sources
    from campaigniq.persistence.approved_forex_month import reference
    app = DeploymentMonthlyApplication(c.inputs, c.publisher, publication_enabled=True)
    account, draft, revision, scope = _setup(c, combined)
    label = 'combined_forex_futures_crypto' if combined else 'approved_forex'
    def publish(actor=c.user, rev=revision, forex=True):
        return app.publish(actor, account, draft.id, rev, confirm_publication=True,
            forex_applicable=forex, crypto_applicable=combined)
    def read(actor=c.user):
        return load_deployment_results(c.inputs, actor, account, draft.id, revision)
    before = c.snapshot()
    rejected(PermissionError, lambda: publish(c.foreign))
    rejected(ValueError, lambda: publish(rev=revision-1))
    rejected(ValueError, lambda: publish(forex=False))
    c.publisher.multi_asset_enabled = False
    try: rejected(PermissionError, publish)
    finally: c.publisher.multi_asset_enabled = True
    assert c.snapshot() == before
    passed(label + '_publication_rechecks_owner_revision_applicability_and_activation_without_writes')
    real = AppendOnlyArtifacts.write_text
    suffixes = ['forex-settlement-attributions.json', 'forex-source-resolution.json',
        'forex-approved-generation.json', 'lot-book.json', 'position-journal.json',
        'account-publication-receipt.json', 'finalized.json']
    if combined: suffixes += ['futures.json', 'crypto.json']
    for suffix in suffixes:
        reached = []
        def fail(storage, key, text):
            real(storage, key, text)
            if key == '2026-10-' + suffix:
                reached.append(key)
                raise RuntimeError('Injected complete generation failure')
        with patch.object(AppendOnlyArtifacts, 'write_text', new=fail):
            rejected(RuntimeError, publish)
        assert reached and c.snapshot() == before
    passed(label + '_failure_after_every_financial_component_receipt_and_marker_rolls_back_entire_generation')
    def caught(storage, key, text):
        real(storage, key, text)
        if key == '2026-10-forex-approved-generation.json':
            import psycopg
            try:
                with storage._session._operation() as conn:
                    conn.execute('UPDATE public.artifact_storage_dev SET content=content WHERE false')
            except psycopg.Error: pass
            else: raise AssertionError('Publication role unexpectedly allows retained financial updates')
    with patch.object(AppendOnlyArtifacts,'write_text',new=caught):
        rejected(RuntimeError,publish)
    assert c.snapshot() == before
    passed(label + '_caught_forbidden_sql_cannot_commit_partial_financial_generation')
    barrier, ready, release = Barrier(3), Event(), Event()
    def paused(storage, key, text):
        real(storage, key, text)
        if key == '2026-10-finalized.json':
            ready.set()
            if not release.wait(20): raise RuntimeError('Publication coordination timed out')
    def attempt():
        barrier.wait(timeout=15)
        try: publish(); return 'winner'
        except ValueError: return 'blocked'
    with patch.object(AppendOnlyArtifacts, 'write_text', new=paused), ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(attempt) for _ in range(2)]
        try:
            barrier.wait(timeout=15)
            if not ready.wait(20): raise RuntimeError('Complete generation marker not reached')
            with c.inputs.gateway.transaction(c.user, account) as old:
                assert not old.artifacts.list_keys(prefix='2026-10-')
                rejected(ValueError, read)
                release.set()
                assert sorted(job.result(timeout=45) for job in jobs) == ['blocked','winner']
                assert not old.artifacts.list_keys(prefix='2026-10-')
        finally: release.set()
    passed(label + '_concurrent_publications_have_one_winner_and_repeatable_reader_sees_no_partial_generation')
    after = c.snapshot()
    assert after['rows']['import_draft_documents'] == before['rows']['import_draft_documents']
    assert after['rows']['import_drafts'] == before['rows']['import_drafts']
    result = read()
    assert result.forex_policy_approved and Decimal(result.forex_settled_usd) == -10
    assert Decimal(result.forex_financing_usd) == -1 and Decimal(result.forex_ending_cash_usd) == 989
    assert result.positions == (('SPY','10'),)
    assert result.forex_settlements[0][3] == 'synthetic-existing-forex-campaign'
    with c.inputs.gateway.transaction(c.user, account) as session:
        receipt = json.loads(session.artifacts.read_text('2026-10-account-publication-receipt.json'))
        assert 'approved_forex_generation' in receipt
        if combined:
            assert result.futures_applicable and 'owner-approved' in result.crypto_status
            assert all(k in receipt for k in ('approved_crypto_generation','futures_generation'))
        summaries,_,_ = load_dashboard_sources(session.artifacts)
        assert summaries[-1].forex_financing_pnl == -1
    assert c.snapshot() == after
    passed(label + '_independent_reader_rebuilds_exact_money_original_sources_fifo_identity_and_dashboard_without_writes')
    import psycopg
    real_verify = publication.verify_published
    def forbidden(service, session, *args):
        with session._operation() as conn:
            conn.execute('UPDATE campaigniq_accounts.import_drafts SET revision=revision WHERE false')
        return real_verify(service, session, *args)
    with patch.object(publication,'verify_published',new=forbidden):
        rejected(psycopg.errors.ReadOnlySqlTransaction, read)
    assert c.snapshot() == after
    passed(label + '_independent_result_reader_database_transaction_rejects_raw_sql_writes')
    foreign = c.reader.snapshot(c.user).user(c.identities[1]).id
    c.reader.execute(c.user,c.reader.snapshot(c.user).revision,'set_grant',user_id=foreign,account_id=account,permission=Permission.VIEW)
    assert read(c.foreign) == result
    c.reader.execute(c.user,c.reader.snapshot(c.user).revision,'set_grant',user_id=foreign,account_id=account,permission=None)
    rejected(PermissionError,lambda:read(c.foreign))
    passed(label + '_committed_result_rechecks_view_access_and_revocation')
    # Coherent tamper: rewrite the journal, its hash, the financial descriptor,
    # and receipt. Independent original-source replay must still refuse it.
    keys = ['2026-10-position-journal.json','2026-10-forex-approved-generation.json','2026-10-account-publication-receipt.json']
    with c.owner() as conn:
        originals = {key:conn.execute('SELECT content FROM campaigniq_accounts.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s',(scope,key)).fetchone()[0] for key in keys}
        journal = json.loads(originals[keys[0]])
        journal['entries'][0]['lots'][0]['lot_id'] = 'forged-lot'
        text = json.dumps(journal,sort_keys=True,indent=2)+'\n'
        record = json.loads(originals[keys[1]])
        record['canonical_artifact_sha256'][keys[0]] = sha256(text.encode()).hexdigest()
        record_text = json.dumps(record,sort_keys=True,indent=2)+'\n'
        receipt = json.loads(originals[keys[2]])
        receipt['approved_forex_generation'] = reference(record,record_text)
        for key, value in zip(keys,(text,record_text,json.dumps(receipt))):
            conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',(value,scope,key))
    try:
        corrupt = c.snapshot()
        rejected(ValueError, read)
        assert c.snapshot() == corrupt
    finally:
        with c.owner() as conn:
            for key, text in originals.items():
                conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',(text,scope,key))
    assert read() == result
    passed(label + '_coherently_rehashed_journal_conflict_is_withheld_without_reader_repair')
    if not combined:
        _following_month(c, app, account, scope, passed)


def _following_month(c, app, account, scope, passed):
    from campaigniq.account_selection_rehearsal import synthetic_sources
    from campaigniq.import_draft_bridge import RestoredUpload
    service, user = c.inputs, c.user
    with c.owner() as conn:
        october = dict(conn.execute('SELECT artifact_key,content FROM campaigniq_accounts.artifact_storage_dev WHERE namespace=%s AND artifact_key LIKE %s',(scope,'2026-10-%')).fetchall())
    draft = service.create(user, account, 2026, 11)
    history = ('Account Statement for 24681358 since 10/31/26 through 11/30/26\n\nAccount Trade History\n'
        ',Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n'
        ',11/15/26 10:00:00,STOCK,BUY,+1,TO OPEN,SPY,,,STOCK,100,100,LMT\n\n'
        'Cash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n\n'
        'Forex Statements\n,Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance\n\n')
    originals = synthetic_sources('24681358')
    closing = originals['schwab_brokerage_statement'].content.decode().replace('January 1-31, 2025','November 1-30, 2026').replace('110.0000','11.0000').replace('11,000.00','1,100.00')
    realized = originals['schwab_realized_gain_loss'].content.decode().replace('01/01/2025 to 01/31/2025','11/01/2026 to 11/30/2026')
    revision = 0
    for role,name,text in (('thinkorswim_trade_history','next.csv',history),('schwab_brokerage_statement','closing.txt',closing),('schwab_realized_gain_loss','realized.txt',realized)):
        revision = service.save(user, account, draft.id, revision, role, RestoredUpload(name,'text/plain',text.encode()))
    app.publish(user, account, draft.id, revision, confirm_publication=True,forex_applicable=False,crypto_applicable=False)
    result = load_deployment_results(service,user,account,draft.id,revision)
    assert result.positions == (('SPY','11'),)
    with c.owner() as conn:
        assert dict(conn.execute('SELECT artifact_key,content FROM campaigniq_accounts.artifact_storage_dev WHERE namespace=%s AND artifact_key LIKE %s',(scope,'2026-10-%')).fetchall()) == october
    passed('approved_forex_following_month_commits_from_published_opening_and_previous_generation_is_identical')
