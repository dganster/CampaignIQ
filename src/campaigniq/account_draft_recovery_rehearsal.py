"""Draft recovery checks inside the existing disposable restricted-role clone."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from campaigniq import account_draft_recovery as recovery
from campaigniq.account_forex_cash_sources_rehearsal import rejected


def verify(c,service,user,account,draft,revision,upload,passed):
    def inventory(rev):return service.saved_documents(user,account,draft.id,rev)
    def review(action,rev=revision,document_id=None):
        return service.draft_recovery_preview(user,account,draft.id,rev,action=action,document_id=document_id)
    initial=inventory(revision)
    statement=next(r for r in initial['documents'] if r['role']=='schwab_brokerage_statement')
    planned=review('remove',document_id=statement['id'])
    def execute(actor=user,rev=revision,digest=planned['review_sha256']):
        return service.recover_draft(actor,account,draft.id,rev,action='remove',document_id=statement['id'],reviewed_sha256=digest,confirm=True)
    before=c.snapshot()
    rejected(PermissionError,lambda:execute(actor=c.foreign))
    rejected(ValueError,lambda:execute(rev=revision-1))
    rejected(ValueError,lambda:execute(digest='0'*64))
    real=recovery._write
    def failed(*args):real(*args);raise RuntimeError('Injected failure after recovery and audit write')
    with patch.object(recovery,'_write',new=failed):rejected(RuntimeError,execute)
    assert c.snapshot()==before
    passed('draft_recovery_foreign_stale_changed_review_and_failure_after_audit_preserve_every_row')
    barrier=Barrier(2)
    def race():
        barrier.wait(20)
        try:return execute()
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:race(),range(2)))
    winners=[r for r in results if r is not None];assert len(winners)==1
    revision=winners[0]['revision'];after=inventory(revision)
    old=next(r for r in after['documents'] if r['id']==statement['id'])
    assert old==dict(statement,superseded=True)
    assert sum(r['role'].startswith(recovery.AUDIT_PREFIX) for r in after['documents'])==1
    revision=service.save(user,account,draft.id,revision,'schwab_brokerage_statement',upload)
    checked=service.no_trading_preview(user,account,draft.id,revision)
    revision=service.save_no_trading_confirmation(user,account,draft.id,revision,
        reviewed_sha256=checked['review_sha256'],confirm_no_trading=True,confirm_cash_income=True)
    reset=review('restart',rev=revision)
    assert {r['role'] for r in reset['review']['affected_documents']}=={'schwab_brokerage_statement','no_trading_owner_confirmation_v1'}
    result=service.recover_draft(user,account,draft.id,revision,action='restart',reviewed_sha256=reset['review_sha256'],confirm=True)
    revision=result['revision'];empty=inventory(revision)
    assert all(r['superseded'] or r['role'].startswith(recovery.AUDIT_PREFIX) for r in empty['documents'])
    assert not service.no_trading_status(user,account,draft.id,revision)
    revision=service.save(user,account,draft.id,revision,'schwab_brokerage_statement',upload)
    assert service.no_trading_preview(user,account,draft.id,revision)['review']['financials']['income_usd']=='59.00'
    passed('draft_recovery_concurrent_remove_has_one_winner_restart_preserves_originals_clears_confirmation_and_statement_can_be_saved_again')
    return revision
