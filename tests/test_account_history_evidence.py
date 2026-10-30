from dataclasses import replace
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import pytest
from campaigniq.account_dashboard import ReportSnapshot
from campaigniq.account_history_evidence import bind_history,materialize_history,HistoryEvidence,HistoryOriginal,retained_history_rows
from campaigniq.account_legacy_results_rehearsal import _candidate
from campaigniq.broker_account_binding import BrokerBinding
from campaigniq.ui.position_history_view import _source_matches_manifest,_raw_entries

FIXTURE=Path(__file__).parent/'fixtures/account-month-history.csv'


def sources():
    rows=_candidate('12345678');raw=FIXTURE.read_bytes()
    # The canonical engine candidate includes the published exact-byte hash.
    return ReportSnapshot('authorized-account',rows.items()),raw


def test_verified_original_reuses_existing_manifest_and_trade_reader(tmp_path):
    snapshot,raw=sources();before=dict(snapshot._rows)
    history=bind_history(snapshot,[('original',sha256(raw).hexdigest(),raw)],
        known_bindings={'securities':BrokerBinding.create('securities','12345678')})
    assert history.published_months==('2025-01',) and len(history.originals)==1 and not history.gaps
    root=materialize_history(tmp_path/'private',history)
    path=root/'Account Trade History January 2025.csv'
    assert path.read_bytes()==raw and path.stat().st_mode & 0o777==0o600
    assert _source_matches_manifest(snapshot,root,date(2025,1,1),date(2025,1,31))
    entries=_raw_entries(root,date(2025,1,1),date(2025,1,31))
    assert len(entries)==1 and entries[0]['quantity_change']=='10'
    assert dict(snapshot._rows)==before


def test_duplicate_retained_versions_are_deduplicated_and_content_changes_not_guessed():
    snapshot,raw=sources();digest=sha256(raw).hexdigest()
    history=bind_history(snapshot,[('older',digest,raw),('current',digest,raw),('replacement',sha256(raw+b'x').hexdigest(),raw+b'x')])
    assert len(history.originals)==1 and history.originals[0].content==raw
    assert not history.gaps


@pytest.mark.parametrize('problem',['missing','tampered','different','binding','no_provenance','wrong_period','duplicate_input','bad_digest'])
def test_unverified_sources_withheld_without_modifying_financial_records(problem):
    snapshot,raw=sources();rows=[('original',sha256(raw).hexdigest(),raw)];known=None
    values=dict(snapshot._rows);key='2025-01-import-provenance.json'
    if problem=='missing':rows=[]
    elif problem=='tampered':rows=[('original',sha256(raw).hexdigest(),raw+b'changed')]
    elif problem=='different':rows=[('original',sha256(raw+b'changed').hexdigest(),raw+b'changed')]
    elif problem=='binding':known={'securities':BrokerBinding.create('securities','87654321')}
    elif problem=='no_provenance':values.pop(key)
    else:
        manifest=json.loads(values[key])
        if problem=='wrong_period':manifest['period_end']='2025-02-28'
        elif problem=='duplicate_input':manifest['inputs']+= [next(i for i in manifest['inputs'] if i['role']=='thinkorswim_trade_history')]
        else:next(i for i in manifest['inputs'] if i['role']=='thinkorswim_trade_history')['sha256']='invalid'
        values[key]=json.dumps(manifest)
    snapshot=ReportSnapshot('selected',values.items());before=dict(snapshot._rows)
    evidence=bind_history(snapshot,rows,known_bindings=known)
    assert not evidence.originals and len(evidence.gaps)==1 and evidence.published_months==('2025-01',)
    assert dict(snapshot._rows)==before and '12345678' not in evidence.gaps[0]


def test_materialization_does_not_reuse_shared_folders_or_accept_unsafe_months(tmp_path):
    snapshot,raw=sources();evidence=bind_history(snapshot,[('id',sha256(raw).hexdigest(),raw)])
    root=tmp_path/'existing';root.mkdir();(root/'keep.csv').write_text('unchanged')
    with pytest.raises(ValueError):materialize_history(root,evidence)
    invalid=HistoryEvidence((replace(evidence.originals[0],month='../../outside'),),(),())
    with pytest.raises(ValueError):materialize_history(tmp_path/'new',invalid)
    assert (root/'keep.csv').read_text()=='unchanged' and not (tmp_path/'outside').exists()


@pytest.mark.parametrize('legacy,schema',[(True,'public'),(False,'campaigniq_signedin_accounts_dev')])
def test_source_query_uses_exact_authorized_namespace_and_includes_retained_versions(legacy,schema):
    class Conn:
        def execute(self,sql,params):
            assert schema+'.import_draft_documents' in sql
            assert params==('authorized-workspace','thinkorswim_trade_history')
            assert 'p.workspace=%s' in sql
            assert 'superseded=false' not in sql and "state='draft'" not in sql
            return type('Result',(),{'fetchall':lambda self:[('id','digest',b'raw')]})()
    assert retained_history_rows(Conn(),'authorized-workspace',legacy=legacy)==[('id','digest',b'raw')]
