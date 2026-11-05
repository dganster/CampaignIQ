from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from campaigniq import account_forex_daily_sources as daily
from campaigniq import account_forex_daily_inventory as inventory
from campaigniq import account_forex_financial_candidate as financial
from campaigniq import account_forex_cash_review as cash
from campaigniq.forex_daily_rehearsal_sources import upload
from tests.test_account_forex_inventory_preview import context, service


@pytest.fixture
def confirmed(context,monkeypatch):
    c=context;archive=upload();pdfs=inventory.originals(archive.content)
    result=inventory.evaluate(c.service,c.session,c.draft,archive.content)
    ctx=daily.context(c.service,c.session,result,pdfs)
    receipt=daily.make_receipt(c.session,c.draft,'owner',ctx)
    by_hash={daily.digest(raw):raw for _,raw in pdfs}
    for item in ctx['pdf_inventory']:
        c.docs[item['role']]=(item['stored_filename'],'application/pdf',by_hash[item['pdf_sha256']])
    c.docs[daily.RECEIPT]=('daily.json','text/plain',cash.canonical(receipt))
    c.draft.revision+=len(pdfs)+1
    monkeypatch.setattr(daily,'current_roles',lambda session,draft:{r for r in c.docs if r.startswith(daily.PREFIX)})
    monkeypatch.setattr(daily,'current_owner',lambda service,session:'owner')
    return c


def test_exact_saved_candidate_rebuild_binds_receipts_originals_and_canonical_opening(confirmed):
    c=confirmed;before=deepcopy((c.docs,c.session.artifacts.rows))
    result=financial.preview(c.service,c.session,c.draft)
    body=result['artifact'];calc=body['calculation']
    assert calc['authoritative_opening_matches'] and calc['all_exact_financial_equations_verified']
    assert calc['closes'][0]['reference']=='1001' and calc['closes'][0]['candidate_pl_usd']=='-10.00'
    assert body['source_receipts_verified'] and not body['financial_policy_approved']
    assert body['original_cash_source_differences']['csv_mtd_minus_candidate_usd']==1
    assert body['predecessor_artifact_sha256']
    assert financial.preview(c.service,c.session,c.draft)==result
    assert financial.verify_record(c.service,c.session,c.draft,result['artifact_text'])==json.loads(result['artifact_text'])
    assert (c.docs,c.session.artifacts.rows)==before
    assert all(body[k] is False for k in ('financial_state_written','publication_authorized','financial_policy_approved'))


@pytest.mark.parametrize('change',['account','revision','approval','dollars','duplicate'])
def test_candidate_cannot_be_forged_or_reused_in_another_scope(confirmed,change):
    c=confirmed;r=financial.preview(c.service,c.session,c.draft);body=json.loads(r['artifact_text'])
    if change=='account':body['account_id']='foreign'
    elif change=='revision':body['source_revision']-=1
    elif change=='approval':body['financial_policy_approved']=True
    elif change=='dollars':body['calculation']['totals']['trading_usd']='-9.00'
    text=json.dumps(body)
    if change=='duplicate':text=text[:-1]+',"publication_authorized":false}'
    with pytest.raises(ValueError):financial.verify_record(c.service,c.session,c.draft,text)


@pytest.mark.parametrize('role',[cash.CASH,cash.HISTORY,cash.REPORT,cash.SUPPLEMENT,cash.RECEIPT,daily.RECEIPT,daily.PREFIX+'2026-10-01'])
def test_changed_saved_source_withholds_candidate_without_adoption_or_repair(confirmed,role):
    c=confirmed;name,media,raw=c.docs[role];c.docs[role]=(name,media,raw+b'\nchanged')
    before=deepcopy((c.docs,c.session.artifacts.rows))
    with pytest.raises((ValueError,UnicodeError)):financial.preview(c.service,c.session,c.draft)
    assert (c.docs,c.session.artifacts.rows)==before


def test_absent_published_predecessor_cannot_be_seeded_from_daily_reports(confirmed):
    c=confirmed;del c.session.artifacts.rows['2026-09-finalized.json']
    before=deepcopy(c.session.artifacts.rows)
    with pytest.raises(ValueError):financial.preview(c.service,c.session,c.draft)
    assert c.session.artifacts.rows==before


def test_readonly_service_checks_current_access_draft_and_revision(confirmed):
    c=confirmed;obj,calls=service(c)
    r=obj.forex_financial_candidate_preview('owner','account','draft',c.draft.revision)
    assert r['artifact']['source_receipts_verified'] and calls==[('owner','account',{})]
    for args in [('foreign','account','draft',c.draft.revision),('owner','foreign','draft',c.draft.revision),
        ('owner','account','foreign',c.draft.revision),('owner','account','draft',c.draft.revision-1),('owner','account','draft',True)]:
        with pytest.raises((ValueError,PermissionError,FileNotFoundError)):obj.forex_financial_candidate_preview(*args)


def test_application_delegation_has_no_publication_call():
    from campaigniq.account_deployment_application import DeploymentMonthlyApplication
    app=object.__new__(DeploymentMonthlyApplication)
    app._inputs=SimpleNamespace(forex_financial_candidate_preview=Mock(return_value='candidate'))
    assert app.forex_financial_candidate_preview('u','a','d',3)=='candidate'
    app._inputs.forex_financial_candidate_preview.assert_called_once_with('u','a','d',3)
