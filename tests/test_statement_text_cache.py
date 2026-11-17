from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
import os,subprocess,sys
import pytest
from campaigniq.statement_text_cache import StatementTextCache,using_text_cache,active_text_cache,directory_for_release

RAW=b'%PDF synthetic original'
TEXT='published layout text\n'
def expected(text=TEXT):return dict(sha256=sha256(text.encode()).hexdigest(),byte_size=len(text.encode()))

def test_private_atomic_store_and_fresh_process_hit(tmp_path):
    root=tmp_path/'cache';cache=StatementTextCache(root)
    assert cache.read(RAW,expected()) is None and not root.exists()
    assert cache.store(RAW,expected(),TEXT)
    assert root.stat().st_mode&0o777==0o700
    entry=next(root.glob('*.txt'));assert entry.stat().st_mode&0o777==0o600
    assert active_text_cache() is None
    with using_text_cache(root):assert active_text_cache().read(RAW,expected())==TEXT
    assert active_text_cache() is None
    driver='''import sys
sys.path.insert(0,sys.argv[1])
from campaigniq.statement_text_cache import StatementTextCache
from hashlib import sha256
text='published layout text\\n'
assert StatementTextCache(sys.argv[2]).read(b'%PDF synthetic original',dict(sha256=sha256(text.encode()).hexdigest(),byte_size=len(text.encode())))==text
print('new-process hit')'''
    result=subprocess.run([sys.executable,'-I','-c',driver,str(Path(__file__).resolve().parents[1]/'src'),str(root)],capture_output=True,text=True,check=True)
    assert result.stdout.strip()=='new-process hit'

@pytest.mark.parametrize('case',('digest','size','raw','provenance','permissions','hardlink','symlink'))
def test_untrusted_entry_or_changed_source_cannot_supply_text(tmp_path,case):
    root=tmp_path/'cache';cache=StatementTextCache(root);assert cache.store(RAW,expected(),TEXT)
    entry=next(root.glob('*.txt'));control=expected();raw=RAW
    outside=tmp_path/'outside';outside.write_text(TEXT);outside.chmod(0o600)
    if case=='digest':entry.write_text('x'*len(TEXT))
    elif case=='size':entry.write_text(TEXT+'extra')
    elif case=='raw':raw+=b'changed'
    elif case=='provenance':control=expected('another published layout')
    elif case=='permissions':entry.chmod(0o644)
    elif case=='hardlink':entry.unlink();os.link(outside,entry)
    else:entry.unlink();entry.symlink_to(outside)
    assert cache.read(raw,control) is None and outside.read_text()==TEXT

@pytest.mark.parametrize('case',('root-link','root-permissions','lock-link','pending-link'))
def test_unsafe_paths_do_not_write_outside_cache(tmp_path,case):
    root=tmp_path/'cache';outside=tmp_path/'outside';outside.write_text('untouched')
    if case=='root-link':root.symlink_to(tmp_path,target_is_directory=True)
    else:
        root.mkdir(mode=0o700)
        if case=='root-permissions':root.chmod(0o755)
        else:(root/('.lock' if case=='lock-link' else '.pending')).symlink_to(outside)
    cache=StatementTextCache(root)
    assert not cache.store(RAW,expected(),TEXT) and outside.read_text()=='untouched'

@pytest.mark.parametrize('control',({},dict(sha256='../outside',byte_size=2),dict(sha256='a'*64,byte_size=True),dict(sha256='a'*64,byte_size=-1),dict(sha256='a'*64,byte_size=8*1024*1024)))
def test_invalid_provenance_never_creates_cache(tmp_path,control):
    cache=StatementTextCache(tmp_path/'cache')
    assert cache.read(RAW,control) is None and not cache.store(RAW,control,TEXT)
    assert not cache.directory.exists()

def test_byte_and_entry_eviction_stays_bounded(tmp_path):
    cache=StatementTextCache(tmp_path/'cache');cache.MAX_ENTRIES=2;cache.MAX_BYTES=20
    for n in range(8):
        text=str(n)*8;assert cache.store(str(n).encode(),expected(text),text)
        entries=list(cache.directory.glob('*.txt'))
        assert len(entries)<=2 and sum(p.stat().st_size for p in entries)<=20
    assert cache.read(b'7',expected('7'*8))=='7'*8

def test_concurrent_writes_leave_only_complete_valid_entries(tmp_path):
    cache=StatementTextCache(tmp_path/'cache')
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda _:cache.store(RAW,expected(),TEXT),range(32)))
    assert cache.read(RAW,expected())==TEXT
    assert len(list(cache.directory.glob('*.txt')))==1 and not (cache.directory/'.pending').exists()

def test_context_is_reset_after_failure(tmp_path):
    with pytest.raises(RuntimeError),using_text_cache(tmp_path/'cache'):
        assert active_text_cache() is not None
        raise RuntimeError('stop')
    assert active_text_cache() is None

def test_only_reviewed_private_runtime_layout_enables_cache(tmp_path):
    digest='a'*64;base=tmp_path/'account-runtime';base.mkdir(mode=0o700)
    path=base/digest[:16]/'release.private.json';path.parent.mkdir(mode=0o700);path.write_text('{}')
    assert directory_for_release(path,digest)==base/'statement-text-v1'
    assert directory_for_release(path,'b'*64) is None
    assert directory_for_release(tmp_path/'release.private.json',digest) is None
    base.chmod(0o777);assert directory_for_release(path,digest) is None

def test_failed_write_is_optional_and_leaves_no_partial_text(tmp_path):
    cache=StatementTextCache(tmp_path/'cache')
    with patch('campaigniq.statement_text_cache.os.replace',side_effect=PermissionError('readonly')):
        assert not cache.store(RAW,expected(),TEXT)
    assert cache.read(RAW,expected()) is None and not (cache.directory/'.pending').exists()
