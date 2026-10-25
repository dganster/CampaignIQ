import base64
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import gzip
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest

from campaigniq import historical_backfill_bundle as capture
from campaigniq import historical_backfill_preflight as audit
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.import_validation import MonthlyInputValidation
from campaigniq.import_preflight import MonthlyImportPreflight
from campaigniq.import_contract import monthly_import_contract, MonthlyInputRole as Role
from campaigniq.monthly_import_execution import MonthlyImportExecution
from campaigniq.closing_inventory_reconciliation import ClosingInventoryReconciliation, ClosingInventoryMismatch
from campaigniq.persistence.position_journal import serialize_position_journal
from campaigniq.persistence.lot_book_store import serialize_lot_book

STAMP='2026-10-23T00:00:00+00:00'


def bundle(months=()):
    result=dict(format=capture.FORMAT, version=1, workspace=capture.WORKSPACE, database='campaigniq_production_db',
                captured_at=STAMP, artifacts={'2026-09-preserved.json':'original'}, drafts=[], documents=[])
    for month in months:
        did=str(uuid4())
        result['drafts'].append(dict(id=did, workspace=capture.WORKSPACE, year=2025, month=month, state='draft', revision=2, created_at=STAMP, updated_at=STAMP))
        for revision in (1,2):
            content=b'original bytes '+str(revision).encode()
            result['documents'].append(dict(id=str(uuid4()), draft_id=did, role=Role.THINKORSWIM_TRADE_HISTORY.value, filename='../../source.csv', media_type='text/csv',
                sha256=sha256(content).hexdigest(), content_base64=base64.b64encode(content).decode(), revision=revision, uploaded_at=STAMP, superseded=revision==1))
    return result


def frame(b, nonce='a'*32):
    raw=capture.encode(b)
    return 'CAMPAIGNIQ_READ_ONLY_EXPORT '+json.dumps(dict(nonce=nonce, sha256=sha256(raw).hexdigest(), gzip_base64=base64.b64encode(gzip.compress(raw)).decode()))


def test_transfer_preserves_all_versions_and_artifacts():
    b=bundle([1]); output='remote banner\n'+frame(b)+'\n'
    assert capture.parse_remote_output(output,'a'*32)==b
    assert len(capture.parse_remote_output(output,'a'*32)['documents'])==2


@pytest.mark.parametrize('mode',['missing','duplicate','nonce','checksum','content','orphan','roles','revision','timezone','workspace'])
def test_capture_rejects_ambiguous_or_corrupt_response(mode):
    b=bundle([1]); output=frame(b)
    if mode=='missing':output='no frame'
    if mode=='duplicate':output+='\n'+output
    if mode=='nonce':output=frame(b,'b'*32)
    if mode=='checksum':output=output.replace(sha256(capture.encode(b)).hexdigest(),'0'*64)
    if mode=='content':b['documents'][0]['content_base64']=base64.b64encode(b'changed').decode();output=frame(b)
    if mode=='orphan':b['documents'][0]['draft_id']=str(uuid4());output=frame(b)
    if mode=='roles':b['documents'][0]['superseded']=False;output=frame(b)
    if mode=='revision':b['documents'][0]['revision']=3;output=frame(b)
    if mode=='timezone':b['captured_at']='2026-10-23T00:00:00';output=frame(b)
    if mode=='workspace':b['workspace']='other';output=frame(b)
    with pytest.raises((ValueError,KeyError)):capture.parse_remote_output(output,'a'*32)


class Connection:
    def __init__(self, failure=False):self.calls=[];self.failure=failure
    def execute(self, sql, params=None):
        self.calls.append((sql,params));self.sql=sql
        if self.failure and 'FROM public.import_drafts ' in sql:raise RuntimeError('query failed')
        return self
    def fetchone(self):return ('campaigniq_production_db','on','repeatable read')
    def fetchall(self):return []


def test_snapshot_transaction_is_read_only_scoped_and_rolled_back():
    c=Connection();b=capture.capture_connection(c,'campaigniq_production_db')
    assert b['database']=='campaigniq_production_db'
    assert c.calls[0][0]=='BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY'
    assert c.calls[-1][0]=='ROLLBACK'
    selects=[(sql,params) for sql,params in c.calls if 'FROM public.' in sql]
    assert len(selects)==3 and all(params==(capture.WORKSPACE,) for _,params in selects)
    assert all('superseded=false' not in sql.lower() for sql,_ in selects)
    assert not any(sql.startswith(('INSERT','UPDATE','DELETE','CREATE')) for sql,_ in c.calls)


def test_snapshot_rolls_back_query_failure():
    c=Connection(True)
    with pytest.raises(RuntimeError):capture.capture_connection(c,'campaigniq_production_db')
    assert c.calls[-1][0]=='ROLLBACK'


def test_remote_code_checks_service_and_cannot_publish():
    text=capture.remote_script('a'*32)
    assert "os.environ.get('RENDER_SERVICE_ID')!=SERVICE" in text
    assert 'default_transaction_read_only=on' in text
    assert 'campaigniq.monthly_import_execution' not in text
    with pytest.raises(ValueError):capture.remote_script('bad')


def result(book=None):
    book=book or LotBook()
    return SimpleNamespace(opening_lot_book=book.clone(),ending_lot_book=book.clone(),position_history=PositionHistory(),campaigns=(),
        forex_transaction_report=None, boundary_reconstruction=SimpleNamespace(unresolved_positions=(),unresolved_campaigns=(),historical_requirements=()))


@pytest.fixture
def mocked_engine(monkeypatch):
    seen=[]
    def prepare(year,month,**kwargs):
        assert year==2025
        with pytest.raises(RuntimeError):kwargs['artifact_storage'].write_text('bad','bad')
        return MonthlyImportPreflight(monthly_import_contract(year,month),tuple(MonthlyInputValidation(r.role,True,"valid") for r in monthly_import_contract(year,month).requirements),None,LotBook())
    def execute(preflight,**kwargs):
        assert kwargs['preview_only'] and 'reconciliation_decision' not in kwargs
        assert 'historical_source_root' not in kwargs
        seen.append(preflight.contract.period_start.month)
        return MonthlyImportExecution(result(preflight.opening_lot_book),ClosingInventoryReconciliation(()),None)
    monkeypatch.setattr(audit,'prepare_monthly_import',prepare)
    monkeypatch.setattr(audit,'execute_monthly_import',execute)
    return seen


def test_all_twelve_drafts_are_previewed_without_external_writes(mocked_engine):
    b=bundle(range(1,13));before=deepcopy(b)
    report=audit.run_preflight(b)
    assert mocked_engine==[1]+[month for month in range(2,13) for _ in (0,1)]
    assert report['all_2025_drafts_passed'] and len(report['months'])==12
    assert len(report['downstream'])==9
    assert report['captured_bytes_unchanged'] and b==before
    assert report['protected_artifact_sha256']['2026-09-preserved.json']==sha256(b'original').hexdigest()
    assert not report['publication_authorized'] and not report['full_portfolio_continuity_verified']
    assert all(len(m['documents'])==1 for m in report['months'])


def test_missing_draft_breaks_chain_but_other_independent_previews_run(mocked_engine):
    report=audit.run_preflight(bundle([1,3,12]))
    assert mocked_engine==[1,3,12]
    assert not report['all_2025_drafts_passed']
    assert report['months'][2]['independent_preview']['closing_reconciled']
    assert 'previous draft' in report['months'][2]['blocking_findings'][0]


def test_reconciliation_failure_is_not_overridden(mocked_engine,monkeypatch):
    def execute(preflight,**kwargs):
        return MonthlyImportExecution(result(),ClosingInventoryReconciliation((ClosingInventoryMismatch(Instrument('META'),Decimal(1),Decimal(2)),)),None)
    monkeypatch.setattr(audit,'execute_monthly_import',execute)
    report=audit.run_preflight(bundle([1,2]))
    assert not report['all_2025_drafts_passed']
    assert report['months'][0]['independent_preview']['closing_mismatches'][0]['broker']=='2'
    assert report['months'][1]['continuous_preview'] is None


def test_monthly_forex_document_is_prepared_and_original_preserved(tmp_path):
    b=bundle([1]);doc=b['documents'][-1].copy();doc.update(role=Role.SCHWAB_FOREX_MONTHLY_STATEMENT.value,filename='../../monthly.txt')
    paths=audit._uploads([doc],tmp_path)
    assert paths[Role.SCHWAB_FOREX_MONTHLY_STATEMENT].parent==tmp_path
    assert paths[Role.SCHWAB_FOREX_MONTHLY_STATEMENT].read_bytes()==base64.b64decode(doc['content_base64'])


def test_unknown_saved_role_blocks_instead_of_discarding(tmp_path):
    doc=bundle([1])['documents'][-1];doc['role']='unknown'
    with pytest.raises(ValueError,match='Unsupported'):audit._uploads([doc],tmp_path)


def book(lid='L1',basis='100'):
    b=LotBook();b.seed(Lot(lid,Instrument('META'),Decimal(1),datetime(2025,1,1),Decimal(basis),'broker','C1'));return b


@pytest.mark.parametrize('change',['none','identity','quantity'])
def test_2026_boundary_uses_opening_journal_not_month_end(change):
    b=bundle();opening=book('DIFFERENT' if change=='identity' else 'L1')
    if change=='quantity':opening=LotBook()
    r=result(opening)
    b['artifacts']['2026-01-position-journal.json']=serialize_position_journal(r,period_start=date(2026,1,1),period_end=date(2026,1,31))
    b['artifacts']['2026-01-lot-book.json']=serialize_lot_book(period_end=date(2026,1,31),lot_book=LotBook())
    row=audit.downstream(audit._FrozenStorage(b),book())[0]
    assert row['quantity_continuity_verified']==(change!='quantity')
    assert row['identity_continuity_verified']==(change=='none')
    assert not row['basis_continuity_verified']


def test_hidden_carried_basis_change_is_reported(mocked_engine,monkeypatch):
    def execute(preflight,**kwargs):
        r=result(preflight.opening_lot_book)
        if preflight.contract.period_start.month==1:r.ending_lot_book=book()
        elif preflight.bootstrap_opening_lot_book.lots(Instrument('META')):r.opening_lot_book=book(basis='101');r.ending_lot_book=book(basis='101')
        return MonthlyImportExecution(r,ClosingInventoryReconciliation(()),None)
    monkeypatch.setattr(audit,'execute_monthly_import',execute)
    monkeypatch.setattr(audit,'capture_position_journal',lambda *args: [])
    report=audit.run_preflight(bundle([1,2]))
    assert not report['months'][1]['exact_opening_state_carried']
    assert any('Pipeline changed' in s for s in report['months'][1]['blocking_findings'])


@pytest.mark.parametrize('market',['forex','crypto'])
def test_applicability_cannot_name_out_of_scope_month(market):
    with pytest.raises(ValueError):audit.run_preflight(bundle(),**{market+'_not_applicable':['2026-01']})


@pytest.mark.parametrize('name,month,total',[('aug',8,'-3040.50'),('sep',9,'-4638.00')])
def test_real_financial_preview_flows_through_consolidated_checks(tmp_path,name,month,total):
    spec=importlib.util.spec_from_file_location("financial_test_helpers",Path(__file__).with_name("test_forex_statement_resolution.py"))
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    execute=helper.execute
    source=Path(__file__).parent/'fixtures'/'forex_resolution'
    for suffix in ('monthly.txt','history.csv','report.csv'):
        (tmp_path/suffix).write_bytes((source/(name+'-'+suffix)).read_bytes())
    end=date(2025,month,31 if month==8 else 30)
    execution=execute((name,tmp_path,end),True)
    report=audit._execution_report(execution,end.replace(day=1))
    assert report['closing_reconciled'] and report['journal_replay_verified']
    assert report['forex_resolution']
    assert execution.result.forex_transaction_report.settlement_pl_usd==Decimal(total)
    assert not (tmp_path/'state').exists()


def test_saved_backup_roundtrip_preserves_superseded_bytes_and_refuses_overwrite(tmp_path):
    from campaigniq.historical_backfill_runner import save_capture
    b=bundle([1]);output=tmp_path/'new'
    restored,verification=save_capture(b,output)
    assert restored==b and len(restored['documents'])==2
    assert verification['workspace_snapshot_roundtrip_verified']
    assert not verification['physical_database_restore_verified']
    assert (output/'workspace-capture.json.gz').stat().st_mode & 0o777==0o600
    assert output.stat().st_mode & 0o777==0o700
    with pytest.raises(FileExistsError):save_capture(b,output)


def test_runner_refuses_render_before_any_capture(monkeypatch):
    from campaigniq.historical_backfill_runner import main
    monkeypatch.setenv('RENDER','true')
    monkeypatch.setattr('sys.argv',['runner','--repo','/unused','--development-only','--render-read-only'])
    with pytest.raises(SystemExit) as e:main()
    assert e.value.code==2


def test_json_duplicate_fields_are_rejected():
    with pytest.raises(ValueError,match='Duplicate'):capture.decode('{"version":1,"version":2}')


def test_database_capture_preserves_document_versions_and_uuid_bytes():
    from uuid import UUID
    b=bundle([1])
    class Rows(Connection):
        def fetchall(self):
            if 'artifact_storage_dev' in self.sql:return list(b['artifacts'].items())
            if 'JOIN' in self.sql:
                return [tuple(base64.b64decode(d['content_base64']) if k=='content' else UUID(d[k]) if k in ('id','draft_id') else datetime.fromisoformat(d[k]) if k=='uploaded_at' else d[k]
                              for k in capture.DOC_FIELDS.split(',')) for d in b['documents']]
            return [tuple(UUID(d[k]) if k=='id' else datetime.fromisoformat(d[k]) if k in ('created_at','updated_at') else d[k]
                          for k in capture.DRAFT_FIELDS.split(',')) for d in b['drafts']]
    actual=capture.capture_connection(Rows(),'campaigniq_production_db')
    assert actual['documents']==b['documents'] and actual['drafts']==b['drafts']


@pytest.mark.parametrize('tamper',[False,True])
def test_offline_runner_saves_report_or_rejects_tamper_without_ssh(tmp_path,monkeypatch,tamper):
    from campaigniq import historical_backfill_runner as runner
    source=tmp_path/'saved';runner.save_capture(bundle(),source)
    if tamper:
        path=source/'workspace-capture.json.gz';path.write_bytes(path.read_bytes()+b'changed')
    monkeypatch.delenv('RENDER',raising=False);monkeypatch.delenv('RENDER_SERVICE_ID',raising=False)
    def git(cmd,**kwargs):
        assert cmd[0]=='git'
        return 'feature/historical-backfill-2025\n' if cmd[-1]=='--show-current' else 'a'*40+'\n'
    monkeypatch.setattr(runner.subprocess,'check_output',git)
    monkeypatch.setattr(runner.subprocess,'run',lambda *args,**kwargs:pytest.fail('offline runner attempted SSH'))
    output=tmp_path/'result'
    monkeypatch.setattr('sys.argv',['runner','--repo',str(tmp_path/'repo'),'--development-only','--capture',str(source/'workspace-capture.json.gz'),'--output',str(output)])
    if tamper:
        with pytest.raises(SystemExit) as error:runner.main()
        assert error.value.code==1 and not output.exists()
    else:
        runner.main()
        report=json.loads((output/'preflight-report.json').read_text())
        assert len(report['months'])==12 and not report['publication_authorized']
        assert report['capture_verification']['workspace_snapshot_roundtrip_verified']
