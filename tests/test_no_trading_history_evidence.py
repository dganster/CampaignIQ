from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
import pytest

from campaigniq.account_dashboard import ReportSnapshot
from campaigniq.account_deployment_dashboard import DeploymentAccountDashboard
from campaigniq.account_history_evidence import bind_history
from tests.test_account_history_evidence import sources
from tests.test_account_no_trading import carry,save,publish
from tests.test_gain_loss_association import saving,bound_context,context


def statement_only_snapshot():
    snapshot,_=sources();rows=dict(snapshot._rows)
    key='2025-01-import-provenance.json';manifest=json.loads(rows[key])
    manifest['inputs']=[r for r in manifest['inputs'] if r['role']!='thinkorswim_trade_history']
    rows[key]=json.dumps(manifest)
    rows['2025-01-no-trading-month.json']='{"format":"campaigniq.no_trading_month","version":1}'
    return ReportSnapshot('authorized-account',rows.items())


def test_marker_alone_never_suppresses_a_missing_source_warning():
    snapshot=statement_only_snapshot();before=dict(snapshot._rows)
    assert len(bind_history(snapshot,[]).gaps)==1
    verified=bind_history(snapshot,[],verified_no_trading_months=('2025-01',))
    assert not verified.gaps and not verified.originals
    assert verified.published_months==('2025-01',) and dict(snapshot._rows)==before


@pytest.mark.parametrize('problem',['other_month','wrong_period','duplicate_trade_input','missing_trade_original'])
def test_exemption_does_not_hide_unrelated_or_invalid_evidence(problem):
    snapshot=statement_only_snapshot();rows=dict(snapshot._rows);key='2025-01-import-provenance.json'
    manifest=json.loads(rows[key]);verified=('2025-01',)
    if problem=='other_month':verified=('2025-02',)
    elif problem=='wrong_period':manifest['period_end']='2025-02-28'
    else:
        entry=dict(role='thinkorswim_trade_history',sha256='0'*64,byte_size=10)
        manifest['inputs']+=[entry]*(2 if problem=='duplicate_trade_input' else 1)
    rows[key]=json.dumps(manifest);snapshot=ReportSnapshot('selected',rows.items())
    assert len(bind_history(snapshot,[],verified_no_trading_months=verified).gaps)==1


def dashboard(c):
    # Retain the real publication and independent result readers; adapt only
    # the database boundary to the existing isolated in-memory account fixture.
    result=object.__new__(DeploymentAccountDashboard);result.inputs=c.associated
    result.choices=lambda user:SimpleNamespace(accounts=(SimpleNamespace(id=c.account,legacy=False),))
    @contextmanager
    def transaction(user,account):
        with c.associated.gateway.transaction(user,account) as session:
            session._check=lambda:None
            session.retained_rows=tuple((k,v) for k,v in c.artifacts[account].rows.items() if k.startswith('2026-09-'))
            session._connection=SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchall=lambda:[]))
            yield session
    result.gateway=SimpleNamespace(_continue_existing=False,transaction=transaction)
    return result


def test_real_statement_only_publication_has_no_october_history_gap(carry):
    c=carry;save(c);publish(c);before=deepcopy(c.artifacts[c.account].rows)
    snapshot=dashboard(c).snapshot(c.admin,c.account,include_history=True)
    assert '2026-10' in snapshot.history.published_months
    assert not any(g.startswith('2026-10:') for g in snapshot.history.gaps)
    assert not any(o.month=='2026-10' for o in snapshot.history.originals)
    assert c.artifacts[c.account].rows==before


@pytest.mark.parametrize('suffix',['no-trading-month.json','account-publication-receipt.json','position-journal.json'])
def test_dashboard_never_exempts_tampered_statement_only_publication(carry,suffix):
    c=carry;save(c);publish(c);c.artifacts[c.account].rows['2026-10-'+suffix]='{}'
    before=deepcopy(c.artifacts[c.account].rows)
    with pytest.raises((ValueError,KeyError)):
        dashboard(c).snapshot(c.admin,c.account,include_history=True)
    assert c.artifacts[c.account].rows==before
