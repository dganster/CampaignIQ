"""Commit and undo the reviewed correction proposals on a local full restore."""
import argparse
import base64
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import subprocess
import uuid

from campaigniq.historical_backfill_bundle import decode, validate, LIMIT
from campaigniq.historical_backfill_publication_package import digest
from campaigniq.historical_backfill_correction_preview import build_preview
from campaigniq.historical_backfill_restored_publisher import (
    connect_local, local_database, lock_tables, table_manifest, require_workspace, save,
)
from campaigniq.importers.schwab.realized_gain_loss_reader import read_realized_gain_loss_section
from campaigniq.persistence.realized_attribution_store import deserialize_realized_attributions

WORKSPACE='workspace-dennis'
TARGETS={'2026-02-realized-attributions.json','2026-04-realized-attributions.json'}


def verified_preview(artifacts,records,preview,proposals,source_pdf):
    if (preview.get('format')!='campaigniq.published_correction_preview'
        or preview.get('version')!=1 or preview.get('workspace')!=WORKSPACE
        or preview.get('corrections_approved') is not False
        or preview.get('publication_authorized') is not False
        or preview.get('read_only_database_verified') is not True):
        raise ValueError('Expected the unchanged, unapproved read-only correction preview.')
    rebuilt,expected=build_preview(artifacts,records,digest(source_pdf))
    for name in ('april_source_pdf_sha256','changes','existing_artifact_sha256','proposed_artifact_sha256'):
        if preview.get(name)!=rebuilt[name]:raise ValueError('Preview differs from independently rebuilt evidence: '+name)
    if set(proposals)!=TARGETS or proposals!=expected:
        raise ValueError('Proposed artifact bytes differ from the verified corrections.')
    return expected


def conditional_update(connection,key,before,after,updated_at):
    if key not in TARGETS:raise ValueError('Correction target is outside the reviewed scope.')
    result=connection.execute('UPDATE public.artifact_storage_dev SET content=%s,updated_at=now() WHERE namespace=%s AND artifact_key=%s AND content=%s AND updated_at=%s',
        (after,WORKSPACE,key,before,updated_at))
    if result.rowcount!=1:raise ValueError('Original correction target changed; transaction must abort.')


class InjectedFailure(Exception):pass


def apply_trial(connection,originals,proposals,audit_key,audit_text,*,inject=False):
    if set(originals)!=TARGETS or set(proposals)!=TARGETS:
        raise ValueError('Exactly the two reviewed artifact targets are required.')
    # Original bytes, row timestamps, source PDF and reviewed plan commit together.
    connection.execute('INSERT INTO public.artifact_storage_dev(namespace,artifact_key,content) VALUES (%s,%s,%s)',(WORKSPACE,audit_key,audit_text))
    for index,key in enumerate(sorted(TARGETS)):
        text,updated_at=originals[key]
        conditional_update(connection,key,text,proposals[key],updated_at)
        if inject and index==0:raise InjectedFailure('controlled correction rollback')


def experiment(connection,bundle,preview,proposals,source_pdf,output):
    local_database(connection.info.dbname)
    with connection.transaction():
        lock_tables(connection);require_workspace(connection,bundle)
        baseline=table_manifest(connection)
        if baseline!=preview['all_user_table_manifest']:
            raise ValueError('Full restored database differs from the reviewed preview baseline.')
        originals={}
        for key in sorted(TARGETS):
            row=connection.execute('SELECT content,updated_at FROM public.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s',(WORKSPACE,key)).fetchone()
            if row is None or row[0]!=bundle['artifacts'][key]:raise ValueError('Original artifact row differs.')
            originals[key]=row
    audit_key='correction-rehearsal-'+uuid.uuid4().hex+'.json'
    audit=dict(format='campaigniq.correction_rehearsal_evidence',version=1,scope='isolated_local_restore',
        original_artifacts={k:dict(content=v[0],updated_at=v[1].isoformat()) for k,v in originals.items()},
        source_pdf_sha256=digest(source_pdf),source_pdf_base64=base64.b64encode(source_pdf).decode(),
        correction_preview=preview,publication_authorized=False)
    audit_text=json.dumps(audit,sort_keys=True,indent=2)
    save(output/'recovery-receipt.json',dict(database=connection.info.dbname,
        original_artifacts=audit['original_artifacts'],audit_artifact_key=audit_key,
        audit_artifact_sha256=digest(audit_text.encode()),proposed_artifact_sha256=preview['proposed_artifact_sha256'],
        baseline_table_manifest=baseline,publication_authorized=False))
    with connection.transaction():
        lock_tables(connection);require_workspace(connection,bundle)
        if table_manifest(connection)!=baseline:raise ValueError('Restored database changed before rollback test.')
        try:
            with connection.transaction():apply_trial(connection,originals,proposals,audit_key,audit_text,inject=True)
        except InjectedFailure:pass
        if table_manifest(connection)!=baseline:raise ValueError('Injected correction did not roll back exactly.')
    expected=dict(bundle['artifacts'],**proposals);expected[audit_key]=audit_text
    committed=False
    try:
        with connection.transaction():
            lock_tables(connection);require_workspace(connection,bundle)
            if table_manifest(connection)!=baseline:raise ValueError('Restored database changed before correction commit.')
            apply_trial(connection,originals,proposals,audit_key,audit_text)
            require_workspace(connection,bundle,expected)
        committed=True
        with connect_local(connection.info.dbname) as observer:
            with observer.transaction():
                observer.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                require_workspace(observer,bundle,expected)
                for key in TARGETS:deserialize_realized_attributions(expected[key])
                recovered=decode(expected[audit_key].encode())
                if base64.b64decode(recovered['source_pdf_base64'])!=source_pdf:
                    raise ValueError('Archived PDF differs after commit.')
                if recovered['original_artifacts']!=audit['original_artifacts']:
                    raise ValueError('Archived original rows differ after commit.')
    finally:
        if committed:
            with connection.transaction():
                lock_tables(connection);require_workspace(connection,bundle,expected)
                for key,(text,timestamp) in originals.items():
                    result=connection.execute('UPDATE public.artifact_storage_dev SET content=%s,updated_at=%s WHERE namespace=%s AND artifact_key=%s AND content=%s',
                        (text,timestamp,WORKSPACE,key,proposals[key]))
                    if result.rowcount!=1:raise ValueError('Correction changed before rollback; inspect retained recovery receipt.')
                result=connection.execute('DELETE FROM public.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s AND content=%s',(WORKSPACE,audit_key,audit_text))
                if result.rowcount!=1:raise ValueError('Audit archive changed before rollback.')
                require_workspace(connection,bundle)
                if table_manifest(connection)!=baseline:raise ValueError('Correction rollback differs from original user-table rows.')
    with connect_local(connection.info.dbname) as observer:
        with observer.transaction():
            observer.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            require_workspace(observer,bundle)
            if table_manifest(observer)!=baseline:raise ValueError('Correction rollback is not visible from a fresh connection.')
    return dict(format='campaigniq.correction_rehearsal',version=1,local_restored_database=connection.info.dbname,
        correction_transaction_rollback_verified=True,committed_corrections_verified=True,
        original_artifact_and_source_archive_verified=True,exact_all_user_table_rollback_verified=True,
        original_artifact_sha256={k:digest(v[0].encode()) for k,v in originals.items()},
        proposed_artifact_sha256=preview['proposed_artifact_sha256'],source_pdf_sha256=digest(source_pdf),
        production_connection_made=False,corrections_approved=False,full_portfolio_continuity_verified=False,
        publication_authorized=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--capture',type=Path,required=True)
    parser.add_argument('--preview',type=Path,required=True)
    parser.add_argument('--april-realized-report',type=Path,required=True)
    parser.add_argument('--local-restored-database',required=True)
    parser.add_argument('--backup',type=Path,required=True)
    parser.add_argument('--backup-sha256',required=True)
    parser.add_argument('--development-only',required=True,action='store_true')
    args=parser.parse_args();local_database(args.local_restored_database)
    repo=args.repo.expanduser().resolve()
    if Path(__file__).resolve().parents[2]!=repo:parser.error('Use the installed development module.')
    def git(*parts):return subprocess.check_output(['git','-C',str(repo),*parts],text=True).strip()
    if git('branch','--show-current')!='feature/historical-backfill-2025' or git('status','--porcelain'):
        parser.error('Use the clean, committed development checkout.')
    if digest(args.backup.expanduser().read_bytes())!=args.backup_sha256:parser.error('Full backup checksum differs.')
    capture=args.capture.expanduser();verification=decode(capture.with_name('capture-verification.json').read_bytes())
    if digest(capture.read_bytes())!=verification['compressed_file_sha256']:parser.error('Capture checksum differs.')
    with gzip.open(capture,'rb') as stream:raw=stream.read(LIMIT+1)
    if len(raw)>LIMIT or digest(raw)!=verification['capture_sha256']:parser.error('Capture content checksum differs.')
    bundle=validate(decode(raw));path=args.preview.expanduser();preview=decode(path.read_bytes())
    if preview.get('capture_sha256')!=verification['capture_sha256']:parser.error('Preview capture differs.')
    if preview.get('local_restored_database')!=args.local_restored_database:parser.error('Preview restore target differs.')
    if subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',preview['development_commit'],'HEAD']).returncode:
        parser.error('Preview commit is not an ancestor of this checkout.')
    pdf=args.april_realized_report.expanduser();pdf_bytes=pdf.read_bytes()
    if digest(pdf_bytes)!=preview['april_source_pdf_sha256']:parser.error('Reviewed PDF checksum differs.')
    result=subprocess.run(['pdftotext','-layout',str(pdf),'-'],capture_output=True,text=True,check=True,timeout=60)
    records=read_realized_gain_loss_section(result.stdout.splitlines())
    proposals={key:path.with_name(key).read_text() for key in TARGETS}
    proposals=verified_preview(bundle['artifacts'],records,preview,proposals,pdf_bytes)
    output=Path.home()/'Downloads'/('CampaignIQ-correction-rehearsal-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    output.mkdir(mode=0o700,exist_ok=False)
    print('Testing corrections on the LOCAL full restore; no production connection.',flush=True)
    try:
        with connect_local(args.local_restored_database) as connection:
            report=experiment(connection,bundle,preview,proposals,pdf_bytes,output)
    except Exception as error:
        save(output/'failure.json',dict(error_type=type(error).__name__,message=str(error) if isinstance(error,ValueError) else 'Database test failed; preserve the backup, restore and diagnostics.',publication_authorized=False))
        parser.exit(1,'Local correction test failed. Diagnostics: '+str(output)+'\n')
    report.update(development_commit=git('rev-parse','HEAD'),preview_file_sha256=digest(path.read_bytes()),
        capture_sha256=verification['capture_sha256'],backup_sha256=args.backup_sha256)
    save(output/'correction-rehearsal-report.json',report)
    print('Injected correction rollback: PASS\nCommitted corrections and original/source archive: PASS\nExact rollback across all user tables: PASS')
    print('Report:',output/'correction-rehearsal-report.json')
    print('Local restore returned to its original state. No production changes.')


if __name__=='__main__':main()
