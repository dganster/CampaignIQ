"""Source-bound income reads and performance on the disposable restricted-role clone."""
from decimal import Decimal
from hashlib import sha256
from unittest.mock import patch

from campaigniq.account_administration import Permission
from campaigniq.account_deployment_application import DeploymentMonthlyApplication
from campaigniq.account_deployment_dashboard import DeploymentAccountDashboard
from campaigniq.account_deployment_results import load_deployment_results
from campaigniq.account_forex_cash_sources_rehearsal import rejected
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.account_income_evidence import legacy_statement_rows
from campaigniq.dashboard_sources import load_dashboard_sources
from campaigniq.account_dashboard import ReportSnapshot
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.income_rehearsal_sources import controls


def verify_income(c,passed):
    service,user,reader=c.inputs,c.user,c.reader
    owner=reader.snapshot(user).user(c.identities[0]).id
    name='Synthetic active month income performance'
    reader.execute(user,reader.snapshot(user).revision,'add_account',owner_id=owner,name=name,broker='schwab')
    account=next(a.id for a in reader.snapshot(user).accounts if a.name==name)
    service.gateway.bind(user,account,'securities','24681361',expected_revision=reader.snapshot(user).revision)
    uploads=synthetic_sources('24681361');original=uploads['schwab_brokerage_statement']
    cash=controls(deposits='0.00',withdrawals='0.00',sales='0.00').replace('$11,000.00 $10,000.00','$11,056.75 $11,000.00')
    uploads['schwab_brokerage_statement']=RestoredUpload(original.name,original.media_type,original.content+cash.encode())
    draft=service.create(user,account,2025,1);revision=0
    for role,upload in uploads.items():revision=service.save(user,account,draft.id,revision,role,upload)
    app=DeploymentMonthlyApplication(service,c.publisher,publication_enabled=True)
    app.publish(user,account,draft.id,revision,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
    before=c.snapshot()
    def read(actor=user):return load_deployment_results(service,actor,account,draft.id,revision)
    result=read();income=result.income_evidence
    assert income.status=='verified' and income.income.net_income==Decimal('56.75')
    assert (income.income.dividends,income.income.credited_interest,income.income.interest_expense,income.income.other_expense)==(
        Decimal('59'),Decimal('.25'),Decimal('-2'),Decimal('-.50'))
    assert income.income.account_investment_result==Decimal('56.75') and Decimal(result.brokerage_realized_usd)==0
    snapshot=DeploymentAccountDashboard(reader).snapshot(user,account)
    summaries,_,_=load_dashboard_sources(snapshot)
    assert summaries[0].realized_plus_statement_income==Decimal('56.75') and summaries[0].combined_realized_pnl==0
    assert all(item.campaign_id is None for item in income.income.items)
    assert c.snapshot()==before
    passed('income_active_month_reader_and_dashboard_include_dividends_interest_and_costs_without_changing_trade_totals_or_rows')
    no_trade=next(a.id for a in reader.snapshot(user).accounts if a.name=='Synthetic no trading statement month')
    no_draft=next(d for d in service.drafts(user,no_trade) if (d.year,d.month)==(2026,10))
    before_no=c.snapshot();no_result=load_deployment_results(service,user,no_trade,no_draft.id,no_draft.revision)
    assert no_result.income_evidence.status=='verified' and no_result.income_evidence.income.net_income==59
    with service.gateway.transaction(user,no_trade) as session:
        rows=[(k,session.artifacts.read_text(k)) for k in session.artifacts.list_keys()]
        summary,_,_=load_dashboard_sources(ReportSnapshot(session.account.storage_scope,rows,income=(no_result.income_evidence,)))
        assert summary[-1].realized_plus_statement_income==59 and summary[-1].combined_realized_pnl==0
    assert c.snapshot()==before_no
    passed('income_no_trading_dividend_increases_income_inclusive_result_once_while_canonical_trading_stays_zero')
    foreign=reader.snapshot(user).user(c.identities[1]).id
    reader.execute(user,reader.snapshot(user).revision,'set_grant',user_id=foreign,account_id=account,permission=Permission.VIEW)
    assert read(c.foreign)==result
    reader.execute(user,reader.snapshot(user).revision,'set_grant',user_id=foreign,account_id=account,permission=None)
    rejected(PermissionError,lambda:read(c.foreign))
    passed('income_original_statement_reader_rechecks_account_scope_view_access_and_revocation')
    import psycopg
    from campaigniq import account_income_evidence as module
    real=module.current_statement
    def forbidden(service,session,*args):
        with session._operation() as conn:conn.execute('UPDATE campaigniq_accounts.import_drafts SET revision=revision WHERE false')
        return real(service,session,*args)
    before_read=c.snapshot()
    with patch.object(module,'current_statement',new=forbidden):rejected(psycopg.errors.ReadOnlySqlTransaction,read)
    assert c.snapshot()==before_read
    passed('income_reader_database_transaction_rejects_raw_sql_writes_and_preserves_every_row')
    # The historical query uses only the scoped existing public original tables.
    with reader._connection(readonly=True) as conn:
        assert conn.execute("SELECT current_setting('transaction_read_only')").fetchone()==('on',)
        legacy_statement_rows(conn,'workspace-andrew')
    assert c.snapshot()==before_read
    passed('income_legacy_statement_query_uses_actual_restricted_readonly_role_without_writes')
    # Coherent row checksum tamper cannot replace the published source receipt.
    with c.owner() as conn:
        row=conn.execute('SELECT id,sha256,content FROM campaigniq_accounts.import_draft_documents WHERE draft_id=%s AND role=%s AND NOT superseded',
            (draft.id,'schwab_brokerage_statement')).fetchone()
        changed=bytes(row[2]).replace(b'59.00',b'99.00')
        conn.execute('UPDATE campaigniq_accounts.import_draft_documents SET sha256=%s,content=%s WHERE id=%s',(sha256(changed).hexdigest(),changed,row[0]))
    try:
        corrupt=c.snapshot();rejected(ValueError,read);assert c.snapshot()==corrupt
    finally:
        with c.owner() as conn:conn.execute('UPDATE campaigniq_accounts.import_draft_documents SET sha256=%s,content=%s WHERE id=%s',(row[1],row[2],row[0]))
    assert read()==result
    passed('income_changed_original_and_row_checksum_are_withheld_by_published_receipt_without_repair')
