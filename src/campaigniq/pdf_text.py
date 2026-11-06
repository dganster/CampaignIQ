"""PDF-to-text extraction boundary with bounded reuse of identical input bytes."""
from __future__ import annotations
from collections import OrderedDict
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
import shutil
import subprocess

class PdfTextExtractionError(RuntimeError):
    """Raised when a PDF source document cannot be extracted safely."""

# Pure extraction output only: no authorization, receipt or financial-result cache.
_CACHE_MAX_BYTES=32*1024*1024
_CACHE_MAX_ENTRIES=128
_cache=OrderedDict()
_cache_bytes=0
_cache_lock=RLock()

def clear_extraction_cache():
    global _cache_bytes
    with _cache_lock:
        _cache.clear();_cache_bytes=0

def _tool_identity(executable):
    try:
        stat=Path(executable).stat()
        return (executable,stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
    except OSError:return (executable,None)

def _cached(key,raw):
    with _cache_lock:
        entry=_cache.get(key)
        if entry is None or entry[0]!=raw:return None
        _cache.move_to_end(key)
        return entry[1]

def _remember(key,raw,text):
    global _cache_bytes
    cost=len(raw)+len(text)
    if cost>_CACHE_MAX_BYTES:return
    with _cache_lock:
        prior=_cache.pop(key,None)
        if prior is not None:_cache_bytes-=len(prior[0])+len(prior[1])
        _cache[key]=(raw,text);_cache_bytes+=cost
        while _cache_bytes>_CACHE_MAX_BYTES or len(_cache)>_CACHE_MAX_ENTRIES:
            _,entry=_cache.popitem(last=False);_cache_bytes-=len(entry[0])+len(entry[1])

def extract_pdf_text(source: str|Path,destination: str|Path)->Path:
    """Extract exact current source bytes to layout-preserving UTF-8 text."""
    source_path=Path(source);destination_path=Path(destination)
    executable=shutil.which('pdftotext')
    if executable is None:
        raise PdfTextExtractionError("PDF import requires the 'pdftotext' utility, but it is not available.")
    try:raw=source_path.read_bytes()
    except OSError as exc:raise PdfTextExtractionError(f'Could not read PDF {source_path.name}.') from exc
    key=('poppler-layout-utf8-v1',_tool_identity(executable),sha256(raw).digest(),len(raw))
    extracted=_cached(key,raw)
    if extracted is None:
        # Poppler consumes this immutable private copy, never a path that may change
        # between cache hashing and extraction. Caller filenames are not retained.
        with TemporaryDirectory(prefix='campaigniq-pdf-extract-') as directory:
            snapshot=Path(directory)/'source.pdf';target=Path(directory)/'source.txt'
            snapshot.write_bytes(raw)
            try:completed=subprocess.run([executable,'-layout',str(snapshot),str(target)],check=False,capture_output=True,text=True)
            except OSError as exc:raise PdfTextExtractionError(f'Could not run pdftotext for {source_path.name}.') from exc
            if completed.returncode!=0:
                detail=completed.stderr.strip();message=f'Could not extract text from PDF {source_path.name}.'
                if detail:message+=f' pdftotext reported: {detail}'
                raise PdfTextExtractionError(message)
            if not target.exists():raise PdfTextExtractionError(f'PDF extraction produced no text file for {source_path.name}.')
            try:
                extracted=target.read_bytes();decoded=extracted.decode('utf-8')
            except (OSError,UnicodeError) as exc:raise PdfTextExtractionError(f'Could not read extracted text for {source_path.name}.') from exc
            if not decoded.strip():raise PdfTextExtractionError(f'PDF extraction produced no usable text for {source_path.name}.')
        _remember(key,raw,extracted)
    destination_path.parent.mkdir(parents=True,exist_ok=True)
    destination_path.write_bytes(extracted)
    return destination_path
