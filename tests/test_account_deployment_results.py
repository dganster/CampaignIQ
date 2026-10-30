from contextlib import contextmanager
import json
import pytest
from campaigniq.account_deployment_results import load_deployment_results
from tests.test_account_deployment_publication import deployed,local,month,bound_context,context,FIXTURES,publish

@pytest.fixture
def readable(deployed,monkeypatch):
    c=deployed;publish(c)
    @contextmanager
    def transaction(user,account):
        with c.bound.gateway.transaction(user,account) as session:
            session._check=lambda:None
            yield session
    monkeypatch.setattr(c.publisher.inputs.gateway,'transaction',transaction)
    return c


def test_deployment_reader_reuses_canonical_results_without_row_changes(readable):
    c=readable;before=dict(c.artifacts[c.account].rows);docs=dict(c.repositories[c.account].docs)
    result=load_deployment_results(c.publisher.inputs,c.admin,c.account,c.draft.id,c.revision)
    assert result.month=='2025-01' and result.original_documents==4 and sum(float(q) for _,q in result.positions)==110
    assert c.artifacts[c.account].rows==before and c.repositories[c.account].docs==docs


def test_source_receipt_conflict_blocks_entire_read_without_repair(readable):
    c=readable;key='2025-01-account-publication-receipt.json'
    changed=json.loads(c.artifacts[c.account].rows[key]);changed['draft_revision']=999
    c.artifacts[c.account].rows[key]=json.dumps(changed);before=dict(c.artifacts[c.account].rows)
    with pytest.raises(ValueError):load_deployment_results(c.publisher.inputs,c.admin,c.account,c.draft.id,c.revision)
    assert c.artifacts[c.account].rows==before


def test_foreign_actor_stale_revision_and_forged_service_cannot_read(readable):
    c=readable
    with pytest.raises(PermissionError):load_deployment_results(c.publisher.inputs,c.member,c.account,c.draft.id,c.revision)
    with pytest.raises(ValueError):load_deployment_results(c.publisher.inputs,c.admin,c.account,c.draft.id,0)
    with pytest.raises(ValueError):load_deployment_results(object(),c.admin,c.account,c.draft.id,c.revision)
    with pytest.raises(ValueError):load_deployment_results(c.publisher.inputs,c.admin,c.account,c.draft.id,True)
