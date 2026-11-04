from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
import pytest

from campaigniq import account_crypto_generation as module
from tests.test_account_crypto_statement_sources import crypto,save,rev
from tests.test_gain_loss_association import saving,bound_context,context


def candidate(c):return c.associated.crypto_generation_preview(c.admin,c.account,c.selected.id,rev(c))


def test_saved_owner_sources_produce_exact_readonly_financial_candidate(crypto):
    c=crypto;save(c);repo=c.repositories[c.account];before=deepcopy(repo.__dict__)
    first=candidate(c);second=candidate(c)
    assert first==second and repo.__dict__==before
    assert first['candidate_only'] and first['publication_authorized'] is False
    body=first['artifact'];calculation=body['calculation']
    assert body['approval']==dict(source_receipt_verified=True,account_association='owner_confirmed',broker_verified_account_link=False)
    assert calculation['totals']['full_ending_cost_basis_usd']=='500.00'
    assert calculation['totals']['fees_already_in_trade_cash_usd']=='3.72'
    assert calculation['original_discrepancies_resolved'] is False
    assert calculation['policy_approved'] is False # Pure calculation unchanged; receipt approval is separate.
    assert 'TEST000841' not in first['artifact_text']
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        draft=c.associated._lock(session,c.selected.id)
        actual=module.verify_candidate(c.associated,session,draft,first['artifact_text'])
        assert actual.sha256==first['artifact_sha256']


@pytest.mark.parametrize('change',['account','draft','revision','receipt','policy','approval','cash','quantity','cost','hash','duplicate','numeric_approval','waived_discrepancies'])
def test_tampered_candidate_is_withheld(crypto,change):
    c=crypto;save(c);body=candidate(c)['artifact'];calc=body['calculation']
    if change=='account':body['account_id']='foreign'
    elif change=='draft':body['draft_id']='foreign'
    elif change=='revision':body['source_revision']+=1
    elif change=='receipt':body['source_receipt_sha256']='a'*64
    elif change=='policy':body['policy_id']='unapproved'
    elif change=='approval':body['approval']['broker_verified_account_link']=True
    elif change=='cash':calc['totals']['proposed_ending_cash_usd']='999'
    elif change=='quantity':calc['ending_quantities']['BTC/USD']='999'
    elif change=='cost':calc['totals']['full_ending_cost_basis_usd']='999'
    elif change=='hash':body['calculation_sha256']='f'*64
    elif change=='numeric_approval':body['approval']['source_receipt_verified']=1
    elif change=='waived_discrepancies':
        calc['original_discrepancies_resolved']=True
        body['calculation_sha256']=sha256(module._json(calc).encode()).hexdigest()
    text=module._json(body)
    if change=='duplicate':text=text.rstrip()[:-1]+',"version":1}'
    before=deepcopy(c.repositories[c.account].docs)
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        draft=c.associated._lock(session,c.selected.id)
        with pytest.raises(ValueError):module.verify_candidate(c.associated,session,draft,text)
    assert c.repositories[c.account].docs==before


def test_consistent_but_forged_calculation_cannot_replace_current_sources(crypto):
    c=crypto;save(c);body=candidate(c)['artifact'];calc=body['calculation']
    # Keep every local arithmetic/hash check consistent. Source rebuild must still reject it.
    calc['totals']['preceding_holdings_plus_cash_usd']='1.00'
    calc['totals']['proposed_value_change_after_external_funding_usd']=str(
        module.Decimal(calc['totals']['current_holdings_plus_cash_usd'])-module.Decimal('1.00')-
        module.Decimal(calc['totals']['net_external_funding_usd']))
    body['calculation_sha256']=sha256(module._json(calc).encode()).hexdigest()
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        draft=c.associated._lock(session,c.selected.id)
        with pytest.raises(ValueError,match='current approved originals'):
            module.verify_candidate(c.associated,session,draft,module._json(body))


@pytest.mark.parametrize('case',['missing','stale','foreign','changed_original'])
def test_current_scope_revision_and_originals_required(crypto,case):
    c=crypto
    if case!='missing':save(c)
    revision=rev(c);user=c.admin
    if case=='stale':revision-=1
    elif case=='foreign':user=c.member
    elif case=='changed_original':
        repo=c.repositories[c.account];key=(c.selected.id,'schwab_crypto_statement')
        name,media,raw=repo.docs[key];repo.docs[key]=(name,media,raw+b'\n')
    before=deepcopy(c.repositories[c.account].docs)
    with pytest.raises((ValueError,PermissionError)):
        c.associated.crypto_generation_preview(user,c.account,c.selected.id,revision)
    assert c.repositories[c.account].docs==before


@pytest.mark.parametrize('revision',[True,-1,'1',None])
def test_invalid_revision_rejected_before_connection(revision):
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    service=object.__new__(DeploymentMonthlyInputs)
    with pytest.raises(ValueError):service.crypto_generation_preview(None,'account','draft',revision)


def test_application_delegates_without_publication():
    from campaigniq.account_deployment_application import DeploymentMonthlyApplication
    calls=[];inputs=SimpleNamespace(gateway=SimpleNamespace(_directory=None),
        crypto_generation_preview=lambda *a,**k:calls.append((a,k)))
    app=DeploymentMonthlyApplication(inputs,None,publication_enabled=False)
    app.crypto_generation_preview('actor','account','draft',7)
    assert calls==[(('actor','account','draft',7),{})] and app.publication_enabled is False
