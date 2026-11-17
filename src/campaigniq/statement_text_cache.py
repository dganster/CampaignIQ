"""Optional private cache of text bound to published provenance, never results."""
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from pathlib import Path
import os,re,stat

from campaigniq.navigation_timing import stage

_active=ContextVar('campaigniq_statement_text_cache',default=None)

@contextmanager
def using_text_cache(directory):
    token=_active.set(None if directory is None else StatementTextCache(directory))
    try:yield
    finally:_active.reset(token)

def active_text_cache():return _active.get()

def directory_for_release(path,reviewed_sha256):
    """Only the already reviewed private runtime layout enables persistent I/O."""
    try:
        path=Path(path)
        if (not isinstance(reviewed_sha256,str) or not re.fullmatch('[0-9a-f]{64}',reviewed_sha256) or
            not path.is_absolute() or path.resolve()!=path or path.name!='release.private.json' or
            path.parent.name!=reviewed_sha256[:16] or path.parent.parent.name!='account-runtime'):return None
        base=path.parent.parent;info=base.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o022:return None
        return base/'statement-text-v1'
    except (OSError,ValueError):return None

def _private(info):
    return stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077 and info.st_nlink==1

class StatementTextCache:
    MAX_ENTRY=4*1024*1024
    MAX_BYTES=32*1024*1024
    MAX_ENTRIES=128
    def __init__(self,directory):self.directory=Path(directory)

    def _control(self,raw,expected):
        if type(raw) is not bytes or not isinstance(expected,dict):return None
        digest=expected.get('sha256');size=expected.get('byte_size')
        if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest) or type(size) is not int or not 0<size<=self.MAX_ENTRY:return None
        key=sha256(('published-layout-v1:'+sha256(raw).hexdigest()+':'+str(len(raw))+':'+digest+':'+str(size)).encode()).hexdigest()+'.txt'
        return key,digest,size

    @contextmanager
    def _directory(self,*,create=False):
        fd=None
        try:
            path=self.directory
            if not path.is_absolute() or path.resolve()!=path:raise ValueError('Canonical cache path required')
            if create:
                parent=path.parent.stat()
                if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.getuid() or parent.st_mode&0o022:raise ValueError('Owned cache parent required')
                try:path.mkdir(mode=0o700)
                except FileExistsError:pass
            fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            info=os.fstat(fd)
            if info.st_uid!=os.getuid() or info.st_mode&0o077:raise ValueError('Private cache directory required')
            yield fd
        finally:
            if fd is not None:os.close(fd)

    def read(self,raw,expected):
        control=self._control(raw,expected)
        if control is None:return None
        key,digest,size=control
        try:
            with stage('income_text_cache_read'),self._directory() as root:
                fd=os.open(key,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=root)
                with os.fdopen(fd,'rb') as stream:
                    info=os.fstat(stream.fileno())
                    if not _private(info) or info.st_size!=size:raise ValueError('Unusable cache entry')
                    data=stream.read(size+1)
                if len(data)!=size or sha256(data).hexdigest()!=digest:raise ValueError('Published text digest differs')
                text=data.decode('utf-8')
            with stage('income_text_cache_hit'):return text
        except FileNotFoundError:
            with stage('income_text_cache_miss'):return None
        except (OSError,ValueError,UnicodeError):
            with stage('income_text_cache_rejected'):return None

    def store(self,raw,expected,text):
        control=self._control(raw,expected)
        if control is None or not isinstance(text,str):return False
        key,digest,size=control;data=text.encode('utf-8')
        if len(data)!=size or sha256(data).hexdigest()!=digest:return False
        import fcntl
        try:
            with stage('income_text_cache_write'),self._directory(create=True) as root:
                lock=os.open('.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=root)
                with os.fdopen(lock,'r+b') as stream:
                    lock_info=os.fstat(stream.fileno())
                    if not _private(lock_info) or lock_info.st_size!=0:return False
                    fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    entries=[]
                    with os.scandir(root) as listing:
                        for entry in listing:
                            if entry.name in ('.lock','.pending'):continue
                            if not re.fullmatch('[0-9a-f]{64}\\.txt',entry.name):return False
                            info=entry.stat(follow_symlinks=False)
                            if not _private(info):return False
                            entries.append((info.st_mtime_ns,entry.name,info.st_size))
                            if len(entries)>self.MAX_ENTRIES+1:return False
                    others=sorted(item for item in entries if item[1]!=key)
                    total=sum(item[2] for item in others)+size
                    while others and (len(others)+1>self.MAX_ENTRIES or total>self.MAX_BYTES):
                        _,name,cost=others.pop(0);os.unlink(name,dir_fd=root);total-=cost
                    if size>self.MAX_BYTES:return False
                    try:
                        pending=os.stat('.pending',dir_fd=root,follow_symlinks=False)
                    except FileNotFoundError:pending=None
                    if pending is not None:
                        if not _private(pending):return False
                        os.unlink('.pending',dir_fd=root)
                    fd=os.open('.pending',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=root)
                    try:
                        with os.fdopen(fd,'wb') as output:output.write(data);output.flush();os.fsync(output.fileno())
                        os.replace('.pending',key,src_dir_fd=root,dst_dir_fd=root)
                    finally:
                        try:os.unlink('.pending',dir_fd=root)
                        except FileNotFoundError:pass
            with stage('income_text_cache_store'):return True
        except (OSError,ValueError):return False
