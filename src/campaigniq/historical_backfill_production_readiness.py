"""Prepare a source-bound publication plan and inspect production READ ONLY.

There is deliberately no production write command in this module.
"""
import argparse
from datetime import date, datetime, timezone
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from campaigniq.historical_backfill_bundle import SERVICE, SSH_TARGET, decode
from campaigniq import historical_backfill_bundle as capture_module
from campaigniq.historical_backfill_publication_package import canonical, digest
from campaigniq.historical_backfill_restored_publisher import load_inputs
from campaigniq.historical_backfill_continuity_links import build_links
from campaigniq.historical_backfill_combined_rehearsal import validate_history_review
from campaigniq.historical_backfill_correction_rehearsal import verified_preview, TARGETS
from campaigniq.historical_backfill_portfolio_evidence import (
    MARKET_KEY, BASIS_KEY, CONSUMPTION_KEY, rebuild_checks, require_reviewed_checks,
)
from campaigniq.persistence.monthly_publication import PROTOCOL_FILE, _serialize_publication_protocol

PREFIX = 'CAMPAIGNIQ_PRODUCTION_READINESS '
REHEARSAL_FLAGS = ('injected_transaction_rollback_verified', 'committed_publication_readers_verified',
    'committed_continuity_and_exact_once_accounting_verified', 'original_artifact_and_pdf_archive_verified',
    'exact_all_user_table_rollback_verified', 'committed_dashboard_source_loader_verified',
    'committed_market_ledger_continuity_verified', 'committed_aggregate_basis_provenance_verified',
    'committed_downstream_basis_consumption_verified')


def workspace_manifest(bundle):
    return dict(workspace=bundle['workspace'], database=bundle['database'],
        artifact_sha256={k:digest(v.encode()) for k,v in sorted(bundle['artifacts'].items())},
        draft_count=len(bundle['drafts']), document_version_count=len(bundle['documents']),
        drafts_sha256=digest(canonical(sorted(bundle['drafts'], key=lambda row:row['id']))),
        documents_sha256=digest(canonical(sorted(bundle['documents'], key=lambda row:row['id']))))


def require_rehearsal(report, package, graph, history, review_bytes, correction_bytes, provenance, consumption):
    if (report.get('format') != 'campaigniq.combined_backfill_rehearsal' or report.get('version') != 1
        or report.get('production_connection_made') is not False or report.get('publication_authorized') is not False
        or not re.fullmatch(r'[0-9a-f]{40}', report.get('development_commit',''))
        or not re.fullmatch(r'campaigniq_restore_[0-9]{8}_[0-9]{6}', report.get('local_restored_database',''))):
        raise ValueError('Expected the successful, isolated integrated rehearsal.')
    for flag in REHEARSAL_FLAGS:
        if report.get(flag) is not True:
            raise ValueError('Integrated rehearsal did not verify: '+flag)
    expected = dict(capture_sha256=package['capture_sha256'], continuity_graph_sha256=digest(canonical(graph)),
        historical_artifact_count=package['candidate_artifact_count'], corrected_artifact_count=2,
        unchanged_2026_artifact_count=len(package['protected_2026_artifact_sha256'])-2,
        linked_brokerage_histories=history['linked_history_count'], broker_realized_records=history['realized_record_count'],
        year_end_basis_group_count=provenance['position_count'], consumed_basis_allocation_count=consumption['consumed_broker_allocation_count'],
        linked_history_review_sha256=digest(review_bytes), correction_rehearsal_sha256=digest(correction_bytes))
    if any(report.get(k)!=v for k,v in expected.items()):
        raise ValueError('Integrated rehearsal differs from the exact reviewed inputs.')


def validate_runtime_paths(paths):
    for path in paths:
        if not re.fullmatch(r'src/campaigniq/[A-Za-z0-9_/]+\.py',path) or '..' in path.split('/'):
            raise ValueError('Runtime source path is outside the package.')


def make_plan(bundle, package, candidates, corrections, graph, checks, rehearsal, runtime, archive_id):
    if bundle['database']!='campaigniq_production_db':
        raise ValueError('A verified production capture is required.')
    if set(corrections)!=TARGETS or not re.fullmatch(r'[0-9a-f]{32}',archive_id):
        raise ValueError('Unexpected correction scope or archive identity.')
    validate_runtime_paths(runtime)
    if not runtime or any(not re.fullmatch(r'[0-9a-f]{64}', value) for value in runtime.values()):
        raise ValueError('Runtime source checksums are missing or invalid.')
    archive_key='combined-rehearsal-publication-'+archive_id+'.json'
    payloads=dict(candidates,**checks,**{'historical-continuity-v1.json':canonical(graph).decode()+'\n'})
    if set(payloads)&set(bundle['artifacts']) or archive_key in bundle['artifacts']:
        raise ValueError('A publication insertion target already exists.')
    updates=dict(corrections,**{PROTOCOL_FILE:_serialize_publication_protocol(first_period_end=date(2025,1,31))})
    operations=[dict(action='insert_if_absent',key=k,after_sha256=digest(v.encode())) for k,v in sorted(payloads.items())]
    operations.append(dict(action='insert_original_source_archive_if_absent',key=archive_key,
        content='Original rows and timestamps, reviewed sources, authorization and recovery receipt; created inside the guarded transaction.'))
    operations.extend(dict(action='compare_and_swap',key=k,before_sha256=digest(bundle['artifacts'][k].encode()),
        after_sha256=digest(v.encode())) for k,v in sorted(updates.items()))
    protected={k:v for k,v in package['protected_2026_artifact_sha256'].items() if k not in corrections}
    plan=dict(format='campaigniq.guarded_historical_publication_plan',version=1,workspace=bundle['workspace'],
        service_id=SERVICE, database='campaigniq_production_db', capture_sha256=package['capture_sha256'],
        publication_package_sha256=digest(canonical(package)), integrated_rehearsal_sha256=digest(canonical(rehearsal)),
        rehearsed_commit=rehearsal['development_commit'], verified_backup_sha256=rehearsal['backup_sha256'],
        expected_workspace=workspace_manifest(bundle), required_runtime_source_sha256=runtime,
        payload_sha256={k:digest(v.encode()) for k,v in sorted(dict(payloads,**updates).items())},
        operations=operations, insert_count=len(payloads)+1, update_count=len(updates),
        protected_2026_artifact_sha256=protected, original_corrected_records_action='archive_exact_originals_before_update',
        drafts_and_original_documents_action='preserve_unchanged', contract_mapping_candidates=package['contract_mapping_candidates'],
        required_explicit_authorization=['Publish January–December 2025 from these exact payloads.',
            'Apply only the two reviewed 2026 correction proposals and retain their exact original records.',
            'Approve the listed exact-contract SPXW/SPX mappings; no blanket symbol aliases.'],
        independent_crypto_month_end_evidence_complete=False,
        production_write_implemented=False, production_ready=False, publication_authorized=False)
    return plan,dict(payloads,**updates)


def readiness_script(nonce, runtime_paths):
    """Only a server-enforced read-only snapshot and source-file hashes."""
    validate_runtime_paths(runtime_paths)
    if not re.fullmatch(r'[0-9a-f]{32}',nonce):raise ValueError('Invalid readiness nonce.')
    # The module contains definitions only; its read-only executable suffix is
    # generated here rather than importing anything from the deployed checkout.
    source=inspect.getsource(capture_module)
    helpers='\ndef canonical(value):\n    return json.dumps(value,sort_keys=True,separators=(\",\",\":\"),allow_nan=False).encode()\ndef digest(data):\n    return sha256(data).hexdigest()\n'
    return source+helpers+'\n'+inspect.getsource(workspace_manifest)+'''
import os, psycopg
from pathlib import Path
if os.environ.get('RENDER_SERVICE_ID')!=SERVICE:raise SystemExit('Unexpected Render service.')
try:
    with psycopg.connect(os.environ['CAMPAIGNIQ_DEV_DATABASE_URL'],autocommit=True,connect_timeout=10,
        options='-c default_transaction_read_only=on -c timezone=UTC -c statement_timeout=30000') as connection:
        captured=capture_connection(connection,'campaigniq_production_db')
    paths=json.loads('''+repr(json.dumps(sorted(runtime_paths)))+''')
    roots=[Path.cwd(),Path('/opt/render/project/src')]
    root=next((p for p in roots if (p/'src/campaigniq/__init__.py').is_file()),None)
    runtime={path:(digest((root/path).read_bytes()) if root is not None and (root/path).is_file() and not (root/path).is_symlink() else None) for path in paths}
    data=dict(service_id=SERVICE,database=captured['database'],workspace_manifest=workspace_manifest(captured),
        runtime_source_sha256=runtime,deployed_commit=os.environ.get('RENDER_GIT_COMMIT'),
        database_read_only_verified=True,publication_authorized=False)
except Exception:
    raise SystemExit('Read-only readiness inspection failed; no publication was attempted.')
raw=canonical(data)
print('''+repr(PREFIX)+'''+json.dumps(dict(nonce='''+repr(nonce)+''',sha256=digest(raw),data=data)))
'''


def parse_readiness(output, nonce):
    lines=[line for line in output.splitlines() if line.startswith(PREFIX)]
    if len(lines)!=1:
        raise ValueError('Missing or ambiguous readiness response.')
    envelope=decode(lines[0][len(PREFIX):].encode())
    if set(envelope)!={'nonce','sha256','data'} or envelope['nonce']!=nonce or digest(canonical(envelope['data']))!=envelope['sha256']:
        raise ValueError('Readiness response identity or checksum differs.')
    data=envelope['data']
    if (data.get('service_id')!=SERVICE or data.get('database')!='campaigniq_production_db'
        or data.get('database_read_only_verified') is not True or data.get('publication_authorized') is not False):
        raise ValueError('Readiness service/database/read-only identity differs.')
    return data


def assess_readiness(plan, observed):
    if plan.get('format')!='campaigniq.guarded_historical_publication_plan' or plan.get('publication_authorized') is not False or plan.get('production_write_implemented') is not False:
        raise ValueError('Expected the unapproved preparation plan.')
    if observed.get('service_id')!=plan['service_id'] or observed.get('database')!=plan['database'] or observed.get('database_read_only_verified') is not True:
        raise ValueError('Wrong service, database, or transaction mode.')
    matches=observed['workspace_manifest']==plan['expected_workspace']
    missing_or_changed=[path for path,value in plan['required_runtime_source_sha256'].items()
        if observed['runtime_source_sha256'].get(path)!=value]
    blockers=[]
    if not matches:blockers.append('Production workspace differs from the rehearsed capture. Do not apply this plan.')
    if missing_or_changed:blockers.append('Deployed runtime source differs from the tested development source. A reviewed code deployment is required before publication.')
    if not re.fullmatch(r'[0-9a-f]{40}',observed.get('deployed_commit') or ''):blockers.append('Deployed commit identity is unavailable.')
    return dict(format='campaigniq.production_readiness_review',version=1,plan_sha256=digest(canonical(plan)),
        checked_at=datetime.now(timezone.utc).isoformat(),service_id=plan['service_id'],deployed_commit=observed.get('deployed_commit'),
        production_workspace_matches_reviewed_capture=matches,deployed_source_matches_tested_source=not missing_or_changed,
        changed_or_missing_runtime_files=missing_or_changed,read_only_preconditions_passed=not blockers,
        blocking_findings=blockers,production_database_read_only_verified=True,
        production_write_implemented=False,production_ready=False,publication_authorized=False)


def save(path, value):
    with path.open('xb') as stream:
        os.chmod(path,0o600);stream.write(canonical(value)+b'\n')


def validate_saved_plan(folder):
    plan=decode((folder/'publication-plan.json').read_bytes());marker=decode((folder/'plan-complete.json').read_bytes())
    if marker.get('plan_sha256')!=digest(canonical(plan)) or marker.get('publication_authorized') is not False:
        raise ValueError('Plan completion checksum differs.')
    if plan.get('publication_authorized') is not False or plan.get('production_write_implemented') is not False:
        raise ValueError('Expected an unapproved preparation plan.')
    for key,checksum in plan['payload_sha256'].items():
        if '/' in key or key in ('.','..') or digest((folder/'payloads'/key).read_bytes())!=checksum:
            raise ValueError('Prepared payload checksum or path differs.')
    material=decode((folder/'archive-material.json').read_bytes())
    if digest(canonical(material))!=plan['archive_material_sha256']:
        raise ValueError('Original/source archive material checksum differs.')
    return plan


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('prepare','check'))
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--development-only',action='store_true',required=True)
    p.add_argument('--output',type=Path,required=True)
    for name in ('package','capture','rehearsal-report','correction-preview','correction-rehearsal-report',
                 'linked-history-review','basis-evidence','market-review','basis-report','basis-consumption-report','april-realized-report','backup'):
        p.add_argument('--'+name,type=Path)
    args=p.parse_args();repo=args.repo.expanduser().resolve();output=args.output.expanduser().resolve()
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):p.error('Use the local development checkout.')
    if Path(__file__).resolve().parents[2]!=repo:p.error('Selected repository differs from the installed module.')
    def git(*parts):return subprocess.check_output(['git','-C',str(repo),*parts],text=True).strip()
    if git('branch','--show-current')!='feature/historical-backfill-2025' or git('status','--porcelain'):p.error('Use the clean, committed development branch.')
    if output.is_relative_to(repo):p.error('Save the plan outside the repository.')
    if args.action=='prepare':
        names=('package','capture','rehearsal_report','correction_preview','correction_rehearsal_report','linked_history_review',
            'basis_evidence','market_review','basis_report','basis_consumption_report','april_realized_report','backup')
        if any(getattr(args,name) is None for name in names):p.error('All saved input paths are required for preparation.')
        paths={name:getattr(args,name).expanduser() for name in names}
        package,bundle,candidates=load_inputs(paths['package'],paths['capture'])
        report=decode(paths['rehearsal_report'].read_bytes())
        if subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',report.get('development_commit',''),'HEAD']).returncode:p.error('Integrated rehearsal commit is not an ancestor of this source.')
        if digest(paths['backup'].read_bytes())!=report['backup_sha256']:p.error('Verified backup file checksum differs.')
        preview=decode(paths['correction_preview'].read_bytes());pdf=paths['april_realized_report'].read_bytes()
        if digest(pdf)!=preview['april_source_pdf_sha256']:p.error('Reviewed PDF checksum differs.')
        from campaigniq.importers.schwab.realized_gain_loss_reader import read_realized_gain_loss_section
        text=subprocess.run(['pdftotext','-layout',str(paths['april_realized_report']),'-'],check=True,capture_output=True,text=True,timeout=60).stdout
        proposals={key:paths['correction_preview'].with_name(key).read_text() for key in TARGETS}
        corrections=verified_preview(bundle['artifacts'],read_realized_gain_loss_section(text.splitlines()),preview,proposals,pdf)
        artifacts=dict(bundle['artifacts'],**candidates,**corrections)
        graph=build_links(package['boundary_plan'],artifacts);history=decode(paths['linked_history_review'].read_bytes());validate_history_review(graph,history)
        evidence=decode(paths['basis_evidence'].read_bytes())
        checks=rebuild_checks(bundle,package,artifacts,evidence,digest(paths['correction_preview'].read_bytes()),corrections)
        supplied={key:paths[name].read_text() for key,name in ((MARKET_KEY,'market_review'),(BASIS_KEY,'basis_report'),(CONSUMPTION_KEY,'basis_consumption_report'))}
        require_reviewed_checks(checks,supplied)
        require_rehearsal(report,package,graph,history,paths['linked_history_review'].read_bytes(),paths['correction_rehearsal_report'].read_bytes(),json.loads(checks[BASIS_KEY]),json.loads(checks[CONSUMPTION_KEY]))
        runtime={str(path.relative_to(repo)):digest(path.read_bytes()) for path in sorted((repo/'src/campaigniq').rglob('*.py'))}
        plan,payloads=make_plan(bundle,package,candidates,corrections,graph,checks,report,runtime,uuid.uuid4().hex)
        material=dict(boundary_plan=package['boundary_plan'],basis_evidence_manifest=evidence,
            correction_preview=preview,source_pdf_base64=__import__('base64').b64encode(pdf).decode(),source_pdf_sha256=digest(pdf),
            original_update_contents={key:bundle['artifacts'][key] for key in TARGETS|{PROTOCOL_FILE}},integrated_rehearsal=report)
        plan['archive_material_sha256']=digest(canonical(material))
        output.mkdir(mode=0o700,parents=True,exist_ok=False);(output/'payloads').mkdir(mode=0o700)
        for key,text in payloads.items():
            if '/' in key or key in ('.','..'):raise ValueError('Unexpected payload path.')
            with (output/'payloads'/key).open('xb') as stream:os.chmod(stream.name,0o600);stream.write(text.encode())
        save(output/'publication-plan.json',plan)
        save(output/'archive-material.json',material)
        save(output/'plan-complete.json',dict(plan_sha256=digest(canonical(plan)),publication_authorized=False))
        print('Offline plan prepared:',output/'publication-plan.json')
        print('Planned inserts:',plan['insert_count'],'; planned updates:',plan['update_count'])
        print('Production write command is not enabled. No production connection was made.')
    else:
        plan=validate_saved_plan(output)
        nonce=uuid.uuid4().hex
        response=subprocess.run(['ssh','-T','-oBatchMode=yes','-oStrictHostKeyChecking=yes','-oConnectTimeout=15',SSH_TARGET,'python -'],
            input=readiness_script(nonce,plan['required_runtime_source_sha256']),text=True,capture_output=True,timeout=180)
        if response.returncode:p.error('Read-only SSH inspection failed. Existing host trust and authentication are required; no settings were changed.')
        observed=parse_readiness(response.stdout,nonce);result=assess_readiness(plan,observed)
        path=output/('production-readiness-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]+'.json');save(path,result)
        print('Production workspace matches reviewed capture:',result['production_workspace_matches_reviewed_capture'])
        print('Deployed runtime matches tested source:',result['deployed_source_matches_tested_source'])
        print('Readiness report:',path)
        print('Production was read only. No publication was attempted or authorized.')
        for finding in result['blocking_findings']:print('Blocked:',finding)


if __name__=='__main__':main()
