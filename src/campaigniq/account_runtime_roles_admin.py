"""Launch only the fixed LOCAL rollback rehearsal under the existing postgres OS user.

Normal user stages code and driver dependencies, receives private JSON receipts.
No role elevation, password change, production target or persistent admin service.
"""
import argparse
import base64
from datetime import datetime,timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4
from campaigniq.account_schema_rehearsal import restore_conninfo,DATABASE
from campaigniq.account_data_backend_rehearsal import save

PREFIX='CAMPAIGNIQ_LOCAL_ROLE_REPORT '
DISTRIBUTIONS=('psycopg','psycopg-binary','psycopg-pool','typing_extensions')
MAX_BUNDLE=128*1024*1024
JOBS={'roles':'account_runtime_roles_rehearsal','publication':'account_deployment_publication_rehearsal'}

# Isolated stdlib bootstrap: no dependency imports until the verified private
# bundle is extracted. All elevated work runs as postgres, never as root.
WORKER=r'''
import base64,hashlib,io,json,os,pathlib,pwd,shutil,stat,sys,tempfile,zipfile
PREFIX='CAMPAIGNIQ_LOCAL_ROLE_REPORT '
if pwd.getpwuid(os.getuid()).pw_name!='postgres' or os.getuid()==0:
    raise SystemExit('Existing postgres OS account required; no database connection made')
root=pathlib.Path(tempfile.mkdtemp(prefix='campaigniq-role-worker-'))
os.chmod(root,0o700)
try:
    packed=sys.stdin.buffer.read(128*1024*1024+1)
    if len(packed)>128*1024*1024:raise ValueError('Oversize rehearsal bundle')
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        entries=archive.infolist()
        if len(entries)>10000 or sum(x.file_size for x in entries)>256*1024*1024:
            raise ValueError('Unsafe rehearsal bundle size')
        names=[x.filename for x in entries]
        if len(set(names))!=len(names):raise ValueError('Duplicate rehearsal bundle entry')
        manifest=json.loads(archive.read('bundle.json'))
        if manifest['python']!=list(sys.version_info[:2]):raise ValueError('Python version differs')
        if set(names)!=set(manifest['sha256'])|{'bundle.json'}:raise ValueError('Bundle inventory differs')
        for name,expected in manifest['sha256'].items():
            relative=pathlib.PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or relative.parts[0] not in ('code','deps','tests'):
                raise ValueError('Unsafe bundle path')
            item=archive.getinfo(name)
            if stat.S_ISLNK(item.external_attr>>16):raise ValueError('Symlink bundle entry')
            data=archive.read(item)
            if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('Bundle checksum differs')
            target=root.joinpath(*relative.parts)
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with target.open('xb') as handle:handle.write(data)
            target.chmod(0o600)
    sys.path[:0]=[str(root/'code'),str(root/'deps')]
    import importlib
    job=manifest.get('job','roles')
    jobs={'roles':'account_runtime_roles_rehearsal','publication':'account_deployment_publication_rehearsal'}
    if job not in jobs:raise ValueError('Unknown fixed local rehearsal')
    runner=importlib.import_module('campaigniq.'+jobs[job])
    original_save=runner.save
    def export(path,value):
        original_save(path,value)
        print(PREFIX+json.dumps(dict(name=path.name,data=base64.b64encode(path.read_bytes()).decode())),flush=True)
    runner.save=export
    runner.run_rehearsal(root/'reports',development_only=True)
finally:
    shutil.rmtree(root)
'''


def _add_file(archive,manifest,path,name):
    if path.is_symlink() or not path.is_file():raise ValueError('Unsafe code or dependency file')
    if name in manifest:return
    data=path.read_bytes();manifest[name]=hashlib.sha256(data).hexdigest()
    archive.writestr(name,data)


def build_bundle(destination,*,job='roles'):
    if job not in JOBS:raise ValueError('Unknown local rehearsal')
    """Copy only package Python source and installed PostgreSQL driver files."""
    import zipfile
    import psycopg
    if psycopg.pq.__impl__!='binary':
        raise ValueError('Installed psycopg binary driver required; no administrator command launched')
    source=Path(__file__).resolve().parent
    hashes={}
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*.py')):
            if '__pycache__' not in path.parts:
                if path.resolve()!=path:raise ValueError('Symlink source path prohibited')
                _add_file(archive,hashes,path,'code/campaigniq/'+path.relative_to(source).as_posix())
        for name in DISTRIBUTIONS:
            dist=metadata.distribution(name)
            files=dist.files
            if not files:raise ValueError('Driver distribution lacks file inventory')
            for relative in files:
                if '..' in relative.parts or '__pycache__' in relative.parts:continue
                path=Path(dist.locate_file(relative))
                _add_file(archive,hashes,path,'deps/'+relative.as_posix())
        if job=='publication':
            from campaigniq.account_reconciliation_rehearsal import ROLES
            for name in sorted(ROLES.values()):
                _add_file(archive,hashes,source.parents[1]/'tests/fixtures'/name,'tests/fixtures/'+name)
        archive.writestr('bundle.json',json.dumps(dict(python=list(sys.version_info[:2]),sha256=hashes,job=job),sort_keys=True))
    if destination.stat().st_size>MAX_BUNDLE:raise ValueError('Rehearsal bundle too large')
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def administrator_command():
    executable=Path(sys._base_executable).resolve()
    if not executable.is_file() or not (str(executable).startswith('/usr/bin/') or str(executable).startswith('/usr/local/bin/')):
        raise ValueError('System Python interpreter required; no administrator command launched')
    sudo=shutil.which('sudo')
    if not sudo:raise ValueError('sudo unavailable; nothing changed')
    return [sudo,'-u','postgres','--',str(executable),'-I','-c',WORKER]


def receive_report(output,line):
    value=json.loads(line[len(PREFIX):])
    name=value['name']
    if name not in ('prewrite-recovery-receipt.json','reviewed-grant-plan.json','account-runtime-roles-report.json','reviewed-publication-grant-plan.json','account-deployment-publication-report.json'):
        raise ValueError('Unexpected administrator receipt name')
    data=base64.b64decode(value['data'],validate=True)
    if len(data)>1024*1024:raise ValueError('Oversize administrator receipt')
    parsed=json.loads(data)
    save(output/name,parsed)
    return name,parsed


def run_admin_rehearsal(output,*,development_only,publication=False):
    if type(publication) is not bool:raise ValueError('Explicit publication rehearsal mode required')
    # Retain the ordinary local target/override guard before any sudo process.
    restore_conninfo(development_only=development_only)
    command=administrator_command()
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    received={}
    with tempfile.TemporaryDirectory(prefix='campaigniq-role-stage-') as staging:
        bundle=Path(staging)/'rehearsal.zip'
        bundle_sha=build_bundle(bundle,job='publication') if publication else build_bundle(bundle)
        save(output/'administrator-rehearsal-request.json',dict(database=DATABASE,os_account='postgres',
            bundle_sha256=bundle_sha,normal_user_privileges_changed=False,transaction_policy='forced rollback',
            production_connection_made=False))
        print('Running LOCAL rollback rehearsal as postgres; sudo may request your computer password.',flush=True)
        with bundle.open('rb') as packed:
            process=subprocess.Popen(command,stdin=packed,stdout=subprocess.PIPE,text=True)
            try:
                for line in process.stdout:
                    if line.startswith(PREFIX):
                        name,value=receive_report(output,line);received[name]=value
                    elif not line.startswith('Report:'):print(line,end='',flush=True)
                status=process.wait()
            except BaseException:
                process.terminate();process.wait();raise
    report_name='account-deployment-publication-report.json' if publication else 'account-runtime-roles-report.json'
    report=received.get(report_name)
    if status!=0 or report is None or report.get('passed') is not True:
        raise RuntimeError('Administrator rehearsal did not complete; preserve saved reports. No role elevation was applied to your user.')
    expected={'prewrite-recovery-receipt.json','reviewed-grant-plan.json',report_name}
    if publication:expected.add('reviewed-publication-grant-plan.json')
    if set(received)!=expected:
        raise RuntimeError('Required administrator receipts incomplete')
    print('Report:',output/report_name,flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only',action='store_true',required=True)
    parser.add_argument('--publication',action='store_true')
    args=parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    try:run_admin_rehearsal(Path.home()/'CampaignIQ-Records/Development-Rehearsals'/('Account-runtime-roles-admin-'+stamp),development_only=args.development_only,publication=args.publication)
    except (ValueError,RuntimeError,OSError,metadata.PackageNotFoundError) as error:
        parser.exit(1,str(error)+'\n')


if __name__=='__main__':main()
