"""Original-statement-bound income for immutable account report snapshots. No writes."""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq.statement_income import StatementIncome,parse_statement_income
from campaigniq.broker_account_binding import verify_document
from campaigniq.pdf_text import extract_pdf_text,PdfTextExtractionError

ROLE='schwab_brokerage_statement'

@dataclass(frozen=True)
class IncomeEvidence:
    month:str
    status:str
    reason:str
    income:StatementIncome|None=None
    original_sha256:str|None=None
    published_binding:str|None=None

def statement_text(raw,media):
    if media=='application/pdf':
        with TemporaryDirectory(prefix='campaigniq-income-original-') as folder:
            root=Path(folder);source=root/'statement.pdf';source.write_bytes(raw)
            return extract_pdf_text(source,root/'statement.txt').read_text()
    if media in ('text/plain','text/csv'):return raw.decode('utf-8-sig')
    raise ValueError('Unsupported original statement type')

def read_bound_statement(month,raw,media,known,*,published_binding):
    start=date.fromisoformat(month+'-01');end=start.replace(day=monthrange(start.year,start.month)[1])
    text=statement_text(raw,media)
    verify_document(known,ROLE,text)
    value=parse_statement_income(text,period_start=start,period_end=end)
    return IncomeEvidence(month,'verified','Published-source-bound statement; cash-posting dates, not tax or ex-dividend dates.',value,
        sha256(raw).hexdigest(),published_binding)

def current_statement(service,session,draft):
    month=f'{draft.year:04d}-{draft.month:02d}'
    key=month+'-account-publication-receipt.json'
    receipt=json.loads(session.artifacts.read_text(key))
    try:
        _,media,raw=session.drafts.document(draft.id,ROLE)
        expected=receipt['original_documents'][ROLE]
        if expected!=dict(sha256=sha256(raw).hexdigest(),byte_size=len(raw)):
            raise ValueError('Statement differs from published original receipt')
        return read_bound_statement(month,raw,media,service._known(session),published_binding=sha256(session.artifacts.read_text(key).encode()).hexdigest())
    except (ValueError,KeyError,UnicodeError,FileNotFoundError,PdfTextExtractionError) as error:
        return IncomeEvidence(month,'unverified','Statement income unavailable or unsupported; excluded from income-inclusive totals.')

def legacy_statement_rows(conn,scope):
    return conn.execute('SELECT d.id,d.sha256,d.content,d.media_type,p.year,p.month FROM public.import_draft_documents d '
        'JOIN public.import_drafts p ON p.id=d.draft_id WHERE p.workspace=%s AND d.role=%s ORDER BY d.id',(scope,ROLE)).fetchall()

def legacy_statements(storage,rows,known,months):
    result=[]
    for month in months:
        try:
            start=date.fromisoformat(month+'-01');end=start.replace(day=monthrange(start.year,start.month)[1])
            key=month+'-import-provenance.json';manifest=json.loads(storage.read_text(key))
            if (manifest.get('format')!='campaigniq.monthly_import_provenance' or type(manifest.get('version')) is not int or manifest['version']!=1 or
                manifest.get('period_start')!=str(start) or manifest.get('period_end')!=str(end)):
                raise ValueError('Published statement provenance differs')
            controls=[r for r in manifest['inputs'] if r.get('role')=='schwab_closing_position_snapshot']
            if len(controls)!=1:raise ValueError('One published closing-statement source required')
            expected=controls[0];matched=[]
            for identifier,digest,content,media,year,number in rows:
                if (year,number)!=(start.year,start.month):continue
                raw=bytes(content)
                if sha256(raw).hexdigest()!=digest:continue
                text=statement_text(raw,media);data=text.encode()
                # Legacy provenance bound extracted layout text. Raw originals
                # also qualify where the original generator recorded raw bytes.
                if not any(sha256(candidate).hexdigest()==expected.get('sha256') and len(candidate)==expected.get('byte_size') for candidate in (data,raw)):
                    continue
                matched.append((str(identifier),raw,media,text))
            if not matched:raise ValueError('Matching published original statement is unavailable')
            # Identical extracted originals are one source, never extra income.
            if len({r[3] for r in matched})!=1:raise ValueError('Ambiguous statement originals')
            _,raw,media,_=sorted(matched,key=lambda r:r[0])[0]
            result.append(read_bound_statement(month,raw,media,known,published_binding=sha256(storage.read_text(key).encode()).hexdigest()))
        except (ValueError,KeyError,TypeError,UnicodeError,FileNotFoundError,PdfTextExtractionError):
            result.append(IncomeEvidence(month,'unverified','No supported published-source-bound statement income; not assumed zero.'))
    return tuple(result)
