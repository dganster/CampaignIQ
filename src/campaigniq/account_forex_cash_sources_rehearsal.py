"""Restricted-role Forex original/receipt checks in the fixed disposable LOCAL clone."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from hashlib import sha256
import json
from threading import Barrier, Event
from unittest.mock import patch

from campaigniq import account_forex_cash_review as cash
from campaigniq.account_administration import Permission
from campaigniq.forex_cash_rehearsal_sources import sources
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.account_data_session import _Drafts


def rejected(kind, call):
    try:
        call()
    except kind:
        return
    raise AssertionError('Expected rejection: ' + kind.__name__)


def verify_forex_cash_sources(c, passed):
    service, user, reader = c.inputs, c.user, c.reader
    owner = reader.snapshot(user).user(c.identities[0]).id
    reader.execute(user, reader.snapshot(user).revision, 'add_account', owner_id=owner,
                   name='Synthetic Forex cash source receipts', broker='schwab')
    account = next(a.id for a in reader.snapshot(user).accounts if a.name == 'Synthetic Forex cash source receipts')
    for kind, number in (('securities', '24681357'), ('forex', '8642097')):
        service.gateway.bind(user, account, kind, number, expected_revision=reader.snapshot(user).revision)
    uploads, ledger, supplement = sources()
    draft = service.create(user, account, 2026, 10)
    revision = 0
    for role, upload in uploads.items():
        revision = service.save(user, account, draft.id, revision, role, upload)
    before = c.snapshot()
    review = service.review_forex_cash_sources(user, account, draft.id, revision, ledger, supplement)
    assert c.snapshot() == before
    assert review['calculation']['candidate_trading_usd'] == Decimal('-10')
    assert review['calculation']['csv_mtd_minus_candidate_usd'] == Decimal('1')
    assert not review['publication_authorized'] and not review['financial_state_written']

    def save(actor=user, rev=revision, digest=review['review_sha256'], cash_upload=ledger, **extra):
        return service.save_forex_cash_sources(actor, account, draft.id, rev, cash_upload, supplement,
            reviewed_preview_sha256=digest, confirm_same_account=True, confirm_source_choices=True, **extra)

    rejected(PermissionError, lambda: save(c.foreign))
    rejected(ValueError, lambda: save(rev=0))
    rejected(ValueError, lambda: save(digest='0' * 64))
    wrong = RestoredUpload('wrong.csv', 'text/csv', ledger.content.replace(b'24681357', b'24681358'))
    rejected(ValueError, lambda: save(cash_upload=wrong))
    assert c.snapshot() == before
    passed('forex_review_is_readonly_and_foreign_stale_changed_review_and_wrong_account_save_write_nothing')

    real_upload = _Drafts.upload
    for failure_role in (cash.CASH, cash.SUPPLEMENT, cash.RECEIPT):
        def fail(repository, draft_id, role, *args, **kwargs):
            result = real_upload(repository, draft_id, role, *args, **kwargs)
            if role == failure_role:
                raise RuntimeError('Injected Forex source save failure')
            return result
        with patch.object(_Drafts, 'upload', new=fail):
            rejected(RuntimeError, save)
        assert c.snapshot() == before
    passed('failure_after_each_forex_original_and_receipt_rolls_back_all_versions_and_revision')

    def caught(repository, draft_id, role, *args, **kwargs):
        result = real_upload(repository, draft_id, role, *args, **kwargs)
        if role == cash.SUPPLEMENT:
            import psycopg
            try:
                with repository._session._operation() as conn:
                    conn.execute('UPDATE public.artifact_storage_dev SET content=content WHERE false')
            except psycopg.Error:
                pass
        return result
    with patch.object(_Drafts, 'upload', new=caught):
        rejected(RuntimeError, save)
    assert c.snapshot() == before
    passed('caught_forbidden_sql_cannot_commit_partial_forex_sources')

    barrier, receipt_written, release = Barrier(3), Event(), Event()
    def paused(repository, draft_id, role, *args, **kwargs):
        result = real_upload(repository, draft_id, role, *args, **kwargs)
        if role == cash.RECEIPT:
            receipt_written.set()
            if not release.wait(20):
                raise RuntimeError('Forex source reader coordination timed out')
        return result
    def attempt():
        barrier.wait(timeout=15)
        try:
            return 'winner', save()
        except ValueError:
            return 'blocked', None
    with patch.object(_Drafts, 'upload', new=paused), ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(attempt) for _ in range(2)]
        try:
            barrier.wait(timeout=15)
            if not receipt_written.wait(20):
                raise RuntimeError('Forex source receipt not reached')
            with service.gateway.transaction(user, account) as old:
                assert old.drafts.get(draft.id).revision == revision
                for role in (cash.CASH, cash.SUPPLEMENT, cash.RECEIPT):
                    rejected(FileNotFoundError, lambda role=role: old.drafts.document(draft.id, role))
                release.set()
                results = [job.result(timeout=30) for job in jobs]
                assert old.drafts.get(draft.id).revision == revision
                rejected(FileNotFoundError, lambda: old.drafts.document(draft.id, cash.RECEIPT))
        finally:
            release.set()
    assert sorted(result[0] for result in results) == ['blocked', 'winner']
    saved_revision = next(result[1] for result in results if result[0] == 'winner')
    assert saved_revision == revision + 3
    after = c.snapshot()
    assert save(rev=saved_revision) == saved_revision and c.snapshot() == after
    passed('concurrent_forex_source_saves_have_one_winner_repeat_is_noop_and_repeatable_reader_sees_no_partial_sources')

    def saved(actor=user, rev=saved_revision):
        return service.forex_cash_source_preview(actor, account, draft.id, rev)
    result = saved()
    assert result['source_receipt_verified'] and not result['publication_authorized']
    assert not result['financial_state_written'] and not result['broker_verified_cross_account_link']
    assert result['calculation']['candidate_net_cash_change_usd'] == Decimal('-11')
    assert result['calculation']['csv_mtd_minus_candidate_usd'] == Decimal('1')
    with service.gateway.transaction(user, account) as session:
        current = session.drafts.get(draft.id)
        for role, raw in ((cash.CASH, ledger.content), (cash.SUPPLEMENT, supplement.content),
                          *[(role, upload.content) for role, upload in uploads.items()]):
            assert session.drafts.document(draft.id, role)[2] == raw
        receipt = cash.load_receipt(session, current)
        assert receipt['confirmed_by'] == owner and receipt['broker_verified_account_link'] is False
        assert receipt['confirmed_source_revision'] == revision
        assert '24681357' not in json.dumps(receipt) and '8642097' not in json.dumps(receipt)
    rejected(ValueError, lambda: saved(rev=revision))
    assert c.snapshot() == after
    passed('independent_saved_forex_reader_rebuilds_exact_originals_owner_receipt_and_preserves_monetary_discrepancy_without_writes')

    foreign = reader.snapshot(user).user(c.identities[1]).id
    def command(name, **values):
        return reader.execute(user, reader.snapshot(user).revision, name, **values)
    command('set_grant', user_id=foreign, account_id=account, permission=Permission.VIEW)
    assert saved(c.foreign)['source_receipt_verified']
    rejected(PermissionError, lambda: save(c.foreign, rev=saved_revision))
    command('set_grant', user_id=foreign, account_id=account, permission=Permission.EDIT)
    rejected(PermissionError, lambda: save(c.foreign, rev=saved_revision))
    command('configure_account', account_id=account, archived=True)
    assert saved()['source_receipt_verified']
    rejected(PermissionError, lambda: save(rev=saved_revision))
    command('configure_account', account_id=account, archived=False)
    command('configure_user', user_id=foreign, enabled=False)
    rejected(PermissionError, lambda: saved(c.foreign))
    command('configure_user', user_id=foreign, enabled=True)
    command('set_grant', user_id=foreign, account_id=account, permission=None)
    rejected(PermissionError, lambda: saved(c.foreign))
    passed('forex_view_edit_archive_disabled_and_revoked_access_are_rechecked_and_only_owner_saves')

    evaluate = cash.evaluate
    def readonly_sql(service, session, *args):
        with session._operation() as conn:
            conn.execute('UPDATE campaigniq_accounts.import_drafts SET revision=revision WHERE false')
        return evaluate(service, session, *args)
    import psycopg
    with patch.object(cash, 'evaluate', new=readonly_sql):
        rejected(psycopg.errors.ReadOnlySqlTransaction, saved)
        rejected(psycopg.errors.ReadOnlySqlTransaction,
            lambda: service.review_forex_cash_sources(user, account, draft.id, saved_revision, ledger, supplement))
    passed('forex_review_and_saved_preview_reject_raw_sql_writes')

    # Corrupt only this new synthetic account, then restore exact original bytes.
    for role in (cash.CASH, cash.SUPPLEMENT, cash.HISTORY, cash.REPORT, cash.RECEIPT):
        prior = c.snapshot()
        with c.owner() as conn:
            row = conn.execute('SELECT id,sha256,content FROM campaigniq_accounts.import_draft_documents '
                'WHERE draft_id=%s AND role=%s AND NOT superseded', (draft.id, role)).fetchone()
            changed = bytes(row[2]) + b'\n'
            if role == cash.RECEIPT:
                body = json.loads(row[2]); body['review_sha256'] = '0' * 64
                changed = cash.canonical(body)
            conn.execute('UPDATE campaigniq_accounts.import_draft_documents SET sha256=%s,content=%s WHERE id=%s',
                (sha256(changed).hexdigest(), changed, row[0]))
        try:
            corrupt = c.snapshot()
            rejected(ValueError, saved)
            rejected(ValueError, lambda: save(rev=saved_revision))
            assert c.snapshot() == corrupt
        finally:
            with c.owner() as conn:
                conn.execute('UPDATE campaigniq_accounts.import_draft_documents SET sha256=%s,content=%s WHERE id=%s',
                    (row[1], row[2], row[0]))
        assert c.snapshot() == prior
    passed('changed_forex_originals_and_receipt_withhold_saved_review_without_adoption_or_repair')

    replacement = RestoredUpload('replacement.csv', 'text/csv', ledger.content + b'\n')
    digest = service.review_forex_cash_sources(user, account, draft.id, saved_revision, replacement, supplement)['review_sha256']
    prior = c.snapshot()
    rejected(ValueError, lambda: save(rev=saved_revision, cash_upload=replacement, digest=digest))
    assert c.snapshot() == prior
    for failure_role in (cash.CASH, cash.RECEIPT):
        with patch.object(_Drafts, 'upload', new=fail):
            rejected(RuntimeError, lambda: save(rev=saved_revision, cash_upload=replacement, digest=digest, replace=True))
        assert c.snapshot() == prior
    replacement_revision = save(rev=saved_revision, cash_upload=replacement, digest=digest, replace=True)
    assert replacement_revision == saved_revision + 2
    assert saved(rev=replacement_revision)['source_receipt_verified']
    with c.owner() as conn:
        rows = conn.execute('SELECT content,superseded FROM campaigniq_accounts.import_draft_documents '
            'WHERE draft_id=%s AND role=%s', (draft.id, cash.CASH)).fetchall()
        assert sorted((bytes(raw) == ledger.content, superseded) for raw, superseded in rows) == [(False, False), (True, True)]
        assert conn.execute('SELECT count(*) FROM campaigniq_accounts.import_draft_documents '
            'WHERE draft_id=%s AND role=%s AND superseded', (draft.id, cash.RECEIPT)).fetchone() == (1,)
    assert c.snapshot()['rows']['artifact_storage_dev'] == before['rows']['artifact_storage_dev']
    passed('explicit_forex_replacement_preserves_prior_original_and_receipt_and_financial_artifacts_are_unchanged')
    verify_forex_inventory(c, passed, account, draft, replacement_revision)


def verify_forex_inventory(c, passed, account, draft, revision):
    """Synthetic predecessor fixtures only; inventory service stays SQL read only."""
    from campaigniq import account_forex_inventory_preview as inventory
    from campaigniq.forex_inventory_rehearsal_sources import monthly_upload, predecessor, statement
    from campaigniq.crypto_account_rehearsal_sources import pdf
    from campaigniq.persistence.monthly_publication import PROTOCOL_FILE, _serialize_publication_protocol, _serialize_finalized_month_marker
    from datetime import date
    service, user = c.inputs, c.user
    monthly = monthly_upload()
    def review(actor=user, rev=revision, upload=monthly):
        return service.forex_inventory_preview(actor, account, draft.id, rev, upload)
    before = c.snapshot()
    incomplete = review()
    assert incomplete['statement_inventory_equation_verified']
    assert incomplete['authoritative_predecessor_available'] is False
    assert incomplete['preview_close_allocations'] == []
    assert c.snapshot() == before
    passed('forex_inventory_missing_predecessor_is_explicit_and_does_not_seed_or_write_opening_lots')
    scope = c.reader.authorize(user, account).storage_scope
    fixtures = {PROTOCOL_FILE: _serialize_publication_protocol(first_period_end=date(2026, 9, 30)),
        '2026-09-lot-book.json': predecessor(),
        '2026-09-finalized.json': _serialize_finalized_month_marker(period_end=date(2026, 9, 30))}
    # These are new synthetic clone-only test fixtures, never a source repair.
    with c.owner() as conn:
        for key, text in fixtures.items():
            conn.execute('INSERT INTO campaigniq_accounts.artifact_storage_dev(namespace,artifact_key,content) VALUES(%s,%s,%s)',
                (scope, key, text))
    after = c.snapshot()
    result = review()
    assert result['authoritative_opening_matches'] and result['authoritative_replay_closing_matches']
    assert result['preview_close_allocations'] == [dict(reference='1001', lot_id='synthetic-forex-opening',
        campaign_id='synthetic-existing-forex-campaign', quantity='50000')]
    assert result['publication_authorized'] is False and result['monthly_pdf_saved'] is False
    assert result['original_cash_review']['calculation']['csv_mtd_minus_candidate_usd'] == Decimal('1')
    assert c.snapshot() == after and review() == result
    rejected(PermissionError, lambda: review(c.foreign))
    rejected(ValueError, lambda: review(rev=revision-1))
    rejected(ValueError, lambda: review(upload=RestoredUpload('wrong.pdf', 'application/pdf', pdf(statement('8642098')))))
    assert c.snapshot() == after
    passed('restricted_forex_inventory_reader_uses_actual_pdf_exact_predecessor_lots_and_saved_sources_without_writes')
    foreign = c.reader.snapshot(user).user(c.identities[1]).id
    c.reader.execute(user, c.reader.snapshot(user).revision, 'set_grant', user_id=foreign, account_id=account, permission=Permission.VIEW)
    assert review(c.foreign)['authoritative_opening_matches']
    c.reader.execute(user, c.reader.snapshot(user).revision, 'set_grant', user_id=foreign, account_id=account, permission=None)
    rejected(PermissionError, lambda: review(c.foreign))
    passed('forex_inventory_view_permission_and_revocation_are_rechecked')
    after = c.snapshot()
    original = inventory.evaluate
    def forbidden(service, session, *args):
        with session._operation() as conn:
            conn.execute('UPDATE campaigniq_accounts.import_drafts SET revision=revision WHERE false')
        return original(service, session, *args)
    import psycopg
    with patch.object(inventory, 'evaluate', new=forbidden):
        rejected(psycopg.errors.ReadOnlySqlTransaction, review)
    assert c.snapshot() == after
    key = '2026-09-lot-book.json'
    changed = json.loads(fixtures[key]); changed['lots'][0]['quantity'] = '-40000'
    with c.owner() as conn:
        conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',
            (json.dumps(changed), scope, key))
    try:
        corrupt = c.snapshot()
        rejected(ValueError, review)
        assert c.snapshot() == corrupt
    finally:
        with c.owner() as conn:
            conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',
                (fixtures[key], scope, key))
    assert c.snapshot() == after
    passed('forex_inventory_enforces_readonly_sql_and_conflicting_predecessor_withholds_result_without_repair')
    verify_daily_forex_inventory(c, passed, account, draft, revision)


def verify_daily_forex_inventory(c, passed, account, draft, revision):
    from campaigniq import account_forex_daily_inventory as daily
    from campaigniq.forex_daily_rehearsal_sources import upload
    service, user = c.inputs, c.user
    partial = upload(); complete = upload(complete=True)
    def review(actor=user, rev=revision, archive=partial):
        return service.forex_daily_inventory_preview(actor, account, draft.id, rev, archive)
    before = c.snapshot()
    result = review()
    assert result['combined_inventory_evidence_reconciled'] and result['saved_execution_facts_verified']
    assert result['authoritative_opening_matches'] and result['ledger_tail_no_activity_recorded']
    assert result['full_independent_daily_coverage'] is False
    assert result['independent_month_end_positions_verified'] is False
    assert result['last_independent_daily_boundary'] == '2026-10-07'
    assert result['export_supported_ending_quantities'] == {}
    assert result['preview_close_allocations'] == [dict(reference='1001', lot_id='synthetic-forex-opening',
        campaign_id='synthetic-existing-forex-campaign', quantity='50000')]
    assert result['daily_pdfs_saved'] is False and result['publication_authorized'] is False
    assert result['original_csv_mtd_minus_daily_settled_usd'] == '1.00'
    finished = review(archive=complete)
    assert finished['full_independent_daily_coverage'] and finished['independent_month_end_positions_verified']
    assert c.snapshot() == before and review() == result
    rejected(PermissionError, lambda: review(c.foreign))
    rejected(ValueError, lambda: review(rev=revision-1))
    rejected(ValueError, lambda: review(archive=upload('8642098')))
    assert c.snapshot() == before
    passed('daily_forex_archive_reconciles_actual_pdfs_exact_lots_and_export_tail_without_monthly_statement_or_writes')
    original = daily.evaluate
    def forbidden(service, session, *args):
        with session._operation() as conn:
            conn.execute('UPDATE campaigniq_accounts.import_drafts SET revision=revision WHERE false')
        return original(service, session, *args)
    import psycopg
    with patch.object(daily, 'evaluate', new=forbidden):
        rejected(psycopg.errors.ReadOnlySqlTransaction, review)
    assert c.snapshot() == before
    scope = c.reader.authorize(user, account).storage_scope
    with c.owner() as conn:
        old = conn.execute('SELECT content FROM campaigniq_accounts.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s',
            (scope, '2026-09-lot-book.json')).fetchone()[0]
        changed = json.loads(old); changed['lots'][0]['quantity'] = '-40000'
        conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',
            (json.dumps(changed), scope, '2026-09-lot-book.json'))
    try:
        corrupt = c.snapshot(); rejected(ValueError, review); assert c.snapshot() == corrupt
    finally:
        with c.owner() as conn:
            conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',
                (old, scope, '2026-09-lot-book.json'))
    assert c.snapshot() == before
    passed('daily_forex_review_enforces_readonly_sql_and_rejects_conflicting_published_opening_without_repair')
