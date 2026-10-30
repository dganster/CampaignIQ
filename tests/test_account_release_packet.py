import json
import subprocess
from pathlib import Path
import pytest
from campaigniq.account_release_packet import prepare_packet


def git(repo,*args):return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True).stdout.strip()


def checkout(tmp_path):
    repo=tmp_path/'repo';(repo/'src/campaigniq').mkdir(parents=True)
    (repo/'src/campaigniq/__init__.py').write_text('# synthetic reviewed source\n')
    git(repo,'init','-b','feature/historical-backfill-2025');git(repo,'config','user.email','synthetic@example.invalid');git(repo,'config','user.name','Synthetic')
    git(repo,'add','.');git(repo,'commit','-m','Synthetic reviewed release')
    return repo


def test_offline_packet_is_private_exact_and_explicitly_not_activation_ready(tmp_path):
    repo=checkout(tmp_path);output=tmp_path/'packet'
    result=prepare_packet(repo,output,development_only=True)
    assert result['offline_packet_complete'] and not result['activation_ready'] and not result['production_connection_made']
    manifest=json.loads((output/'release-source-manifest.json').read_text())
    assert manifest['commit']==git(repo,'rev-parse','HEAD') and len(manifest['source_files'])==1
    assert output.stat().st_mode&0o777==0o700
    assert all(path.stat().st_mode&0o777==0o600 for path in output.iterdir())
    before={p.name:p.read_bytes() for p in output.iterdir()}
    with pytest.raises(ValueError):prepare_packet(repo,output,development_only=True)
    assert {p.name:p.read_bytes() for p in output.iterdir()}==before

@pytest.mark.parametrize('case',['dirty','untracked','main','render','not-development'])
def test_unreviewed_checkout_or_live_environment_stops_before_packet(tmp_path,monkeypatch,case):
    repo=checkout(tmp_path)
    if case=='dirty':(repo/'src/campaigniq/__init__.py').write_text('# modified')
    elif case=='untracked':(repo/'src/campaigniq/new.py').write_text('# untracked')
    elif case=='main':git(repo,'checkout','-b','main')
    elif case=='render':monkeypatch.setenv('RENDER','true')
    with pytest.raises(ValueError):prepare_packet(repo,tmp_path/'packet',development_only=case!='not-development')
    assert not (tmp_path/'packet').exists()
