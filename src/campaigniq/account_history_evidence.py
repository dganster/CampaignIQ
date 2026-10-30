"""Read-only, published-checksum-bound originals for one account report snapshot."""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from campaigniq.dashboard_sources import published_artifact_keys
from campaigniq.broker_account_binding import verify_document

ROLE='thinkorswim_trade_history'


@dataclass(frozen=True)
class HistoryOriginal:
    month: str
    content: bytes
    sha256: str
    document_id: str


@dataclass(frozen=True)
class HistoryEvidence:
    originals: tuple[HistoryOriginal,...] = ()
    gaps: tuple[str,...] = ()
    published_months: tuple[str,...] = ()


def retained_history_rows(conn, scope, *, legacy):
    # Fixed schema names only. No filename, draft ID or namespace supplied by
    # the browser is used to route source documents.
    schema='public' if legacy else 'campaigniq_signedin_accounts_dev'
    return conn.execute('SELECT d.id,d.sha256,d.content FROM '+schema+'.import_draft_documents d '
        'JOIN '+schema+'.import_drafts p ON p.id=d.draft_id '
        'WHERE p.workspace=%s AND d.role=%s ORDER BY d.id',(scope,ROLE)).fetchall()


def bind_history(storage, rows, *, known_bindings=None):
    months=tuple(key[:7] for key in published_artifact_keys(storage,suffix='-realized-attributions.json'))
    originals=[];gaps=[]
    for month in months:
        try:
            start=date.fromisoformat(month+'-01');end=start.replace(day=monthrange(start.year,start.month)[1])
            key=month+'-import-provenance.json'
            if not storage.exists(key):raise ValueError('Published source fingerprint is unavailable')
            manifest=json.loads(storage.read_text(key))
            if (type(manifest) is not dict or manifest.get('format')!='campaigniq.monthly_import_provenance'
                or type(manifest.get('version')) is not int or manifest.get('version')!=1
                or manifest.get('period_start')!=start.isoformat() or manifest.get('period_end')!=end.isoformat()
                or type(manifest.get('inputs')) is not list):
                raise ValueError('Published source fingerprint is invalid')
            matches=[item for item in manifest['inputs'] if type(item) is dict and item.get('role')==ROLE]
            if len(matches)!=1:raise ValueError('Published trade-history fingerprint is missing or ambiguous')
            match=matches[0];digest=match.get('sha256');size=match.get('byte_size')
            if (not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest)
                or type(size) is not int or size<1):raise ValueError('Published trade-history fingerprint is invalid')
            candidates=[]
            for identifier,saved_digest,raw in rows:
                if saved_digest!=digest:continue
                content=bytes(raw)
                if len(content)==size and sha256(content).hexdigest()==digest:
                    candidates.append((str(identifier),content))
            if not candidates:raise ValueError('Matching saved original is unavailable or its checksum differs')
            identifier,content=sorted(candidates,key=lambda row:row[0])[0]
            if known_bindings and "securities" in known_bindings:
                try:verify_document(known_bindings,ROLE,content.decode('utf-8-sig'))
                except (ValueError,UnicodeError):raise ValueError('Saved original does not match the current broker binding') from None
            originals.append(HistoryOriginal(month,content,digest,identifier))
        except (ValueError,KeyError,TypeError) as error:
            # No raw content, account numbers or uploaded filenames in UI gaps.
            reason=str(error) if type(error) is ValueError else 'Published source fingerprint could not be read'
            if isinstance(error,json.JSONDecodeError):reason='Published source fingerprint could not be read'
            gaps.append(month+': '+reason+'.')
    return HistoryEvidence(tuple(originals),tuple(gaps),months)


def materialize_history(root, evidence):
    """Write verified bytes only into a newly created private temporary folder."""
    root=Path(root)
    if root.exists() or root.is_symlink():raise ValueError('A new private history directory is required')
    root.mkdir(mode=0o700)
    seen=set()
    for original in evidence.originals:
        if not re.fullmatch('[0-9]{4}-[0-9]{2}',original.month):raise ValueError('Invalid evidence month')
        start=date.fromisoformat(original.month+'-01')
        if original.month in seen:raise ValueError('Duplicate evidence month')
        seen.add(original.month)
        if sha256(original.content).hexdigest()!=original.sha256:raise ValueError('History snapshot checksum differs')
        target=root/f'Account Trade History {start:%B %Y}.csv'
        with os.fdopen(os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as f:
            f.write(original.content)
    return root
