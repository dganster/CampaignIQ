"""Atomic historical/correction/continuity rehearsal on a LOCAL full restore."""
import argparse
import base64
import calendar
from datetime import date, datetime, timezone
import json
from pathlib import Path
import subprocess
import uuid

from campaigniq.historical_backfill_bundle import decode
from campaigniq.historical_backfill_publication_package import digest,canonical
from campaigniq.historical_backfill_candidate_payloads import validate_generation
from campaigniq.historical_backfill_restored_publisher import (
    connect_local,local_database,lock_tables,require_workspace,table_manifest,load_inputs,save,PublishedView,
)
from campaigniq.historical_backfill_correction_rehearsal import verified_preview,conditional_update,TARGETS
from campaigniq.historical_backfill_continuity_links import build_links,verify_partition
from campaigniq.importers.schwab.realized_gain_loss_reader import read_realized_gain_loss_section
from campaigniq.persistence.monthly_publication import PROTOCOL_FILE,_serialize_publication_protocol,is_month_published_in_storage

WORKSPACE='workspace-dennis'
CONTINUITY_KEY='historical-continuity-v1.json'


def validate_history_review(graph,review):
    if (review.get('format')!='campaigniq.linked_history_accounting_review'
        or review.get('scope')!='brokerage_equity_and_options'
        or review.get('publication_authorized') is not False
        or review.get('no_database_connection_made') is not True
        or review.get('continuity_graph_sha256')!=digest(canonical(graph))):
        raise ValueError('Linked history review differs from the exact continuity graph.')
    rebuilt=verify_partition(graph)
    for name in ('histories','totals','linked_history_count','realized_record_count',
        'exact_once_record_partition_verified','broker_monetary_totals_conserved'):
        if review.get(name)!=rebuilt[name]:raise ValueError('Linked history accounting differs: '+name)


class InjectedFailure(Exception):pass


def stage(connection,originals,candidates,corrections,graph_text,audit_key,audit_text,*,inject=False):
    validate_generation(candidates)
    if set(corrections)!=TARGETS or set(originals)!=TARGETS|{PROTOCOL_FILE}:
        raise ValueError('Combined update scope differs.')
    inserts={**candidates,CONTINUITY_KEY:graph_text,audit_key:audit_text}
    if len(inserts)!=len(candidates)+2 or set(inserts)&set(originals):raise ValueError('Combined insert scope overlaps.')
    connection.execute('INSERT INTO public.artifact_storage_dev(namespace,artifact_key,content) VALUES (%s,%s,%s)',(WORKSPACE,audit_key,audit_text))
    for key in sorted(TARGETS):
        before,stamp=originals[key];conditional_update(connection,key,before,corrections[key],stamp)
    for count,key in enumerate(sorted(candidates,key=lambda k:(k.endswith('-finalized.json'),k)),1):
        connection.execute('INSERT INTO public.artifact_storage_dev(namespace,artifact_key,content) VALUES (%s,%s,%s)',(WORKSPACE,key,candidates[key]))
        if inject and count==3:raise InjectedFailure('controlled combined rollback')
    connection.execute('INSERT INTO public.artifact_storage_dev(namespace,artifact_key,content) VALUES (%s,%s,%s)',(WORKSPACE,CONTINUITY_KEY,graph_text))
    before,stamp=originals[PROTOCOL_FILE]
    result=connection.execute('UPDATE public.artifact_storage_dev SET content=%s,updated_at=now() WHERE namespace=%s AND artifact_key=%s AND content=%s AND updated_at=%s',
        (_serialize_publication_protocol(first_period_end=date(2025,1,31)),WORKSPACE,PROTOCOL_FILE,before,stamp))
    if result.rowcount!=1:raise ValueError('Original publication protocol changed; transaction must abort.')


def verify_readers(artifacts,package,review):
    view=PublishedView(artifacts)
    for year,last in ((2025,12),(2026,9)):
        for month in range(1,last+1):
            end=date(year,month,calendar.monthrange(year,month)[1])
            if not is_month_published_in_storage(view,period_end=end):raise ValueError('A period is not visible to the publication reader.')
    stored=decode(artifacts[CONTINUITY_KEY].encode())
    rebuilt=build_links(package['boundary_plan'],artifacts)
    if stored!=rebuilt:raise ValueError('Stored continuity links differ from committed records.')
    validate_history_review(stored,review)


def experiment(connection,bundle,package,candidates,corrections,preview,graph,review,pdf_bytes,output):
    local_database(connection.info.dbname);validate_history_review(graph,review)
    with connection.transaction():
        lock_tables(connection);require_workspace(connection,bundle);baseline=table_manifest(connection)
        if baseline!=preview['all_user_table_manifest']:raise ValueError('Local restore differs from the reviewed database baseline.')
        originals={key:connection.execute('SELECT content,updated_at FROM public.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s',(WORKSPACE,key)).fetchone() for key in TARGETS|{PROTOCOL_FILE}}
        if any(row is None or row[0]!=bundle['artifacts'][key] for key,row in originals.items()):raise ValueError('Original row differs.')
    graph_text=canonical(graph).decode()+'\n';audit_key='combined-rehearsal-'+uuid.uuid4().hex+'.json'
    archive=dict(format='campaigniq.combined_rehearsal_archive',version=1,scope='isolated_local_restore',
        original_artifacts={k:dict(content=v[0],updated_at=v[1].isoformat()) for k,v in originals.items()},
        source_pdf_base64=base64.b64encode(pdf_bytes).decode(),source_pdf_sha256=digest(pdf_bytes),
        capture_sha256=package['capture_sha256'],correction_preview=preview,
        continuity_graph_sha256=digest(canonical(graph)),publication_authorized=False)
    audit_text=canonical(archive).decode()+'\n'
    inserts=dict(candidates,**{CONTINUITY_KEY:graph_text,audit_key:audit_text})
    if set(inserts)&set(bundle['artifacts']):raise ValueError('Refusing to replace an existing insert target.')
    save(output/'recovery-receipt.json',dict(database=connection.info.dbname,original_artifacts=archive['original_artifacts'],
        inserted_artifact_sha256={k:digest(v.encode()) for k,v in inserts.items()},baseline_table_manifest=baseline,publication_authorized=False))
    with connection.transaction():
        lock_tables(connection);require_workspace(connection,bundle)
        if table_manifest(connection)!=baseline:raise ValueError('Database changed before injected rollback.')
        try:
            with connection.transaction():stage(connection,originals,candidates,corrections,graph_text,audit_key,audit_text,inject=True)
        except InjectedFailure:pass
        if table_manifest(connection)!=baseline:raise ValueError('Combined injected transaction did not roll back exactly.')
    expected=dict(bundle['artifacts'],**inserts,**corrections)
    expected[PROTOCOL_FILE]=_serialize_publication_protocol(first_period_end=date(2025,1,31))
    committed=False
    try:
        with connection.transaction():
            lock_tables(connection);require_workspace(connection,bundle)
            if table_manifest(connection)!=baseline:raise ValueError('Database changed before combined commit.')
            stage(connection,originals,candidates,corrections,graph_text,audit_key,audit_text)
            require_workspace(connection,bundle,expected)
        committed=True
        with connect_local(connection.info.dbname) as observer:
            with observer.transaction():
                observer.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                require_workspace(observer,bundle,expected);verify_readers(expected,package,review)
                recovered=decode(expected[audit_key].encode())
                if base64.b64decode(recovered['source_pdf_base64'])!=pdf_bytes or recovered['original_artifacts']!=archive['original_artifacts']:
                    raise ValueError('Committed original/source archive differs.')
    finally:
        if committed:
            with connection.transaction():
                lock_tables(connection);require_workspace(connection,bundle,expected)
                for key,(content,stamp) in originals.items():
                    result=connection.execute('UPDATE public.artifact_storage_dev SET content=%s,updated_at=%s WHERE namespace=%s AND artifact_key=%s AND content=%s',
                        (content,stamp,WORKSPACE,key,expected[key]))
                    if result.rowcount!=1:raise ValueError('A combined update changed before rollback.')
                for key,text in inserts.items():
                    result=connection.execute('DELETE FROM public.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s AND content=%s',(WORKSPACE,key,text))
                    if result.rowcount!=1:raise ValueError('A combined insert changed before rollback.')
                require_workspace(connection,bundle)
                if table_manifest(connection)!=baseline:raise ValueError('Combined rollback differs from original table rows.')
    with connect_local(connection.info.dbname) as observer:
        with observer.transaction():
            observer.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            require_workspace(observer,bundle)
            if table_manifest(observer)!=baseline:raise ValueError('Combined rollback is not visible from a new connection.')
    return dict(format='campaigniq.combined_backfill_rehearsal',version=1,local_restored_database=connection.info.dbname,
        injected_transaction_rollback_verified=True,committed_publication_readers_verified=True,
        committed_continuity_and_exact_once_accounting_verified=True,original_artifact_and_pdf_archive_verified=True,
        exact_all_user_table_rollback_verified=True,historical_artifact_count=len(candidates),
        corrected_artifact_count=len(corrections),unchanged_2026_artifact_count=sum(k.startswith('2026-') and k not in corrections for k in bundle['artifacts']),
        linked_brokerage_histories=review['linked_history_count'],broker_realized_records=review['realized_record_count'],
        continuity_scope='brokerage_equity_and_options',continuity_graph_sha256=digest(canonical(graph)),
        production_connection_made=False,full_portfolio_continuity_verified=False,publication_authorized=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repo','package','capture','correction-preview','correction-rehearsal-report','linked-history-review','april-realized-report','backup'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--local-restored-database',required=True);p.add_argument('--backup-sha256',required=True)
    p.add_argument('--development-only',required=True,action='store_true');args=p.parse_args();local_database(args.local_restored_database)
    repo=args.repo.expanduser().resolve()
    if Path(__file__).resolve().parents[2]!=repo:p.error('Use the installed development module.')
    def git(*parts):return subprocess.check_output(['git','-C',str(repo),*parts],text=True).strip()
    if git('branch','--show-current')!='feature/historical-backfill-2025' or git('status','--porcelain'):p.error('Use the clean, committed development checkout.')
    if digest(args.backup.expanduser().read_bytes())!=args.backup_sha256:p.error('Full backup checksum differs.')
    package,bundle,candidates=load_inputs(args.package.expanduser(),args.capture.expanduser())
    preview_path=args.correction_preview.expanduser();preview=decode(preview_path.read_bytes())
    correction_path=args.correction_rehearsal_report.expanduser();correction=decode(correction_path.read_bytes())
    review_path=args.linked_history_review.expanduser();review=decode(review_path.read_bytes())
    graph=decode(review_path.with_name('continuity-links.json').read_bytes())
    if (correction.get('preview_file_sha256')!=digest(preview_path.read_bytes())
        or review.get('correction_rehearsal_file_sha256')!=digest(correction_path.read_bytes())
        or review.get('package_sha256')!=digest((args.package.expanduser()/'publication-package.json').read_bytes())
        or any(x.get('capture_sha256')!=package['capture_sha256'] for x in (preview,correction,review))
        or correction.get('backup_sha256')!=args.backup_sha256
        or correction.get('local_restored_database')!=args.local_restored_database):p.error('Reviewed input evidence differs.')
    for flag in ('committed_corrections_verified','correction_transaction_rollback_verified','original_artifact_and_source_archive_verified','exact_all_user_table_rollback_verified'):
        if correction.get(flag) is not True:p.error('A successful correction rehearsal is required.')
    if correction.get('production_connection_made') is not False or correction.get('publication_authorized') is not False:p.error('Expected an isolated correction rehearsal.')
    pdf=args.april_realized_report.expanduser();pdf_bytes=pdf.read_bytes()
    if digest(pdf_bytes)!=preview['april_source_pdf_sha256']:p.error('Reviewed PDF checksum differs.')
    text=subprocess.run(['pdftotext','-layout',str(pdf),'-'],check=True,capture_output=True,text=True,timeout=60).stdout
    records=read_realized_gain_loss_section(text.splitlines());proposals={k:preview_path.with_name(k).read_text() for k in TARGETS}
    corrections=verified_preview(bundle['artifacts'],records,preview,proposals,pdf_bytes)
    if graph!=build_links(package['boundary_plan'],dict(bundle['artifacts'],**candidates,**corrections)):p.error('Continuity graph differs from source records.')
    validate_history_review(graph,review)
    output=Path.home()/'Downloads'/('CampaignIQ-combined-rehearsal-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]);output.mkdir(mode=0o700,exist_ok=False)
    print('Testing combined publication on the LOCAL full restore; production is disabled.',flush=True)
    try:
        with connect_local(args.local_restored_database) as connection:
            result=experiment(connection,bundle,package,candidates,corrections,preview,graph,review,pdf_bytes,output)
    except Exception as error:
        save(output/'failure.json',dict(error_type=type(error).__name__,message=str(error) if isinstance(error,ValueError) else 'Database test failed; preserve all inputs and recovery diagnostics.',publication_authorized=False))
        p.exit(1,'Combined local test failed. Diagnostics: '+str(output)+'\n')
    result.update(development_commit=git('rev-parse','HEAD'),backup_sha256=args.backup_sha256,capture_sha256=package['capture_sha256'],
        linked_history_review_sha256=digest(review_path.read_bytes()),correction_rehearsal_sha256=digest(correction_path.read_bytes()))
    save(output/'combined-rehearsal-report.json',result)
    print('Injected combined transaction rollback: PASS\nCommitted publication, corrections and continuity readers: PASS\nOriginal/source archive: PASS\nExact rollback across all user tables: PASS')
    print('Report:',output/'combined-rehearsal-report.json')
    print('Local restore returned to its original state. Full portfolio continuity and production publication remain unverified.')


if __name__=='__main__':main()
