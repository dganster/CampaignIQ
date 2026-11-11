from datetime import date
import pytest
from campaigniq.account_draft_recovery import selection,AUDIT_PREFIX
from campaigniq.broker_account_binding import BrokerBinding
from campaigniq.no_trading_export import check_history


def row(identifier,role,superseded=False):return dict(id=identifier,role=role,superseded=superseded)

def test_remove_invalidates_confirmations_but_preserves_other_sources_and_history():
    rows=[row('a','thinkorswim_trade_history'),row('b','schwab_brokerage_statement'),
          row('c','no_trading_owner_confirmation_v1'),row('d','forex_financial_policy_approval_v1'),
          row('e','thinkorswim_trade_history',True),row('f',AUDIT_PREFIX+'2')]
    assert [r['id'] for r in selection(rows,'remove','a')]==['a','c','d']
    assert [r['id'] for r in selection(rows,'restart',None)]==['a','b','c','d']

@pytest.mark.parametrize('action,identifier',[('remove','missing'),('remove','old'),('remove','audit'),('restart','a'),('delete','a')])
def test_recovery_rejects_invalid_or_historical_selection(action,identifier):
    with pytest.raises(ValueError):selection([row('a','schwab_brokerage_statement'),row('old','x',True),row('audit',AUDIT_PREFIX+'2')],action,identifier)


def export():
    return '''Account Statement for 24681360SCHW (Individual) since 10/1/26 through 10/31/26

Cash Balance
DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE
10/1/26,01:00:00,BAL,,Opening,,,,"8,963.90"
10/20/26,09:00:00,DIV,,DG dividend,,,59.00,"9,022.90"
10/31/26,01:00:00,BAL,,Closing,,,,"9,022.90"

Account Order History
Notes,Time Placed,Symbol,Qty

Account Trade History
Exec Time,Symbol,Qty

Forex Statements
TYPE,AMOUNT,BALANCE
BAL,,0.00
'''

def checked(text):
    return check_history(text.encode(),{'securities':BrokerBinding.create('securities','24681360')},date(2026,10,1),date(2026,10,31),
        dict(opening_cash_usd='8963.90',ending_cash_usd='9022.90',cash_income=[dict(date='2026-10-20',kind='dividend',amount_usd='59.00')]))

def test_balance_only_export_reconciles_income_and_explicit_empty_execution_section():
    assert checked(export())['no_execution_rows']

@pytest.mark.parametrize('old,new',[
    ('Exec Time,Symbol,Qty','Exec Time,Symbol,Qty\n10/20/26,DG,100'),
    ('24681360SCHW','12345678SCHW'),('through 10/31/26','through 10/30/26'),
    ('DG dividend,,,59.00','DG dividend,,,60.00'),('DIV,,DG','TRD,,DG'),
    ('BAL,,0.00','BAL,,1.00'),('Account Trade History','Unknown section'),
    ('DG dividend,,,59.00','DG dividend,1.00,,59.00'),
    ('10/31/26,01:00:00','10/30/26,01:00:00'),
    ('"9,022.90"','"9,02.90"')])
def test_ambiguous_or_active_supporting_export_is_rejected(old,new):
    with pytest.raises(ValueError):checked(export().replace(old,new))

# Exercise the full existing statement-only path with a nonempty cash-only export.
from tests.test_account_no_trading import carry,review,save,publish
from tests.test_gain_loss_association import saving,bound_context,context


def test_cash_only_export_stays_bound_through_owner_confirmation_and_publication(carry):
    c=carry;repo=c.repositories[c.account]
    raw=export().replace('24681360SCHW','12345678SCHW').encode()
    repo.upload(c.selected.id,'thinkorswim_trade_history','October.csv','text/csv',raw,replace=True)
    checked=review(c)
    assert 'thinkorswim_trade_history' in checked['review']['original_documents']
    save(c);publish(c)
    assert repo.document(c.selected.id,'thinkorswim_trade_history')[2]==raw
    from campaigniq.account_deployment_results import _deployment_results_session
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        result=_deployment_results_session(c.associated,session,session.drafts.get(c.selected.id),c.account,repo.get(c.selected.id).revision)
    assert result.no_trading_month and result.cash_income_usd=='59.00' and __import__('decimal').Decimal(result.brokerage_realized_usd)==0
