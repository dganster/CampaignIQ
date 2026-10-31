#!/usr/bin/env python3
"""Start reviewed account UI with private runtime connections on the service disk."""
import json
import os
from pathlib import Path
import stat
import sys

def private_json(path):
    if not path.is_absolute() or path.resolve()!=path:raise ValueError('Canonical private runtime path required')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>1048576:
            raise ValueError('Private bounded runtime file owned by service user required')
        raw=stream.read(1048577)
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate runtime field')
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs)

def main():
    from campaigniq.account_deployment_startup import read_release,compose_release,verify_startup
    from campaigniq.account_release_config import ROLE_ENV
    import tomllib
    root=Path(__file__).resolve().parents[1]
    path=Path(os.environ['CAMPAIGNIQ_ACCOUNT_RUNTIME_FILE'])
    runtime=private_json(path)
    keys={'format','version','release_sha256','connections','credential_packet_sha256'}
    if set(runtime)!=keys or runtime['format']!='campaigniq.private_account_runtime' or type(runtime['version']) is not int or runtime['version']!=1:raise ValueError('Runtime configuration differs')
    if set(runtime['connections'])!={name for _,name in ROLE_ENV}:raise ValueError('Three runtime role connections required')
    release_path=path.with_name('release.private.json')
    config=read_release(release_path,reviewed_sha256=runtime['release_sha256'])
    if config.publication_enabled is not False:raise ValueError('Initial account activation must keep publication disabled')
    auth=tomllib.loads((root/'.streamlit/secrets.toml').read_text())['auth']
    application=compose_release(config,runtime['connections'],auth,repo=root)
    verify_startup(application)
    if sys.argv[1:]==['--check-only']:
        print('Reviewed source, sign-in and all three restricted role startup checks: PASS')
        return
    if sys.argv[1:]:raise ValueError('Unexpected startup arguments')
    os.environ.update(runtime['connections'])
    os.environ['CAMPAIGNIQ_ACCOUNT_RELEASE_FILE']=str(release_path)
    os.environ['CAMPAIGNIQ_ACCOUNT_RELEASE_SHA256']=runtime['release_sha256']
    port=os.environ.get('PORT','8501')
    if not port.isdecimal() or not 1<=int(port)<=65535:raise ValueError('Invalid server port')
    os.execv(sys.executable,[sys.executable,'-m','streamlit','run',str(root/'src/campaigniq/ui/account_deployment_entrypoint.py'),
        '--server.address=0.0.0.0','--server.port='+port,'--server.headless=true'])

if __name__=='__main__':
    try:main()
    except Exception:
        print('Account runtime startup checks failed; private diagnostics required.',file=sys.stderr)
        raise SystemExit(1)
