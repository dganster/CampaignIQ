"""Explicit retained-account continuation; public sources remain immutable.

This adapter is opt-in in development composition. It changes no schema,
privilege, live release setting, account identity or publication availability.
"""
from datetime import date
import re
import json
from types import MappingProxyType
from campaigniq.account_dashboard import ReportSnapshot
from campaigniq.account_legacy_results import published_months, _results

FRONTIER='2026-09'

def expected_months(scope):
    if scope=='workspace-dennis':
        return tuple(f'{y:04d}-{m:02d}' for y in (2025,2026) for m in range(1,13) if y==2025 or m<=9)
    if scope=='workspace-andrew':return tuple(f'2026-{m:02d}' for m in range(1,10))
    raise ValueError('Retained continuation requires an explicitly reviewed workspace')

def future_month(year,month):
    if type(year) is not int or type(month) is not int:raise ValueError('Integer reporting month required')
    value=date(year,month,1)
    if not 1900<=year<=2200 or value<=date(2026,9,1):
        raise ValueError('Retained published history is immutable; select October 2026 or later')

def future_key(key):
    if key=='.campaigniq-monthly-publication-v1.json':return
    match=re.fullmatch(r'([0-9]{4})-([0-9]{2})-.+',key)
    if match is None:raise ValueError('Continuation artifacts require a reporting month')
    future_month(*map(int,match.groups()))

def validate_protocol(content):
    value=json.loads(content)
    if type(value) is not dict or type(value.get('version')) is not int or value!=dict(format='campaigniq.monthly_publication',version=1,first_protected_period_end='2026-10-31'):
        raise ValueError('Continuation publication boundary differs')

class ContinuationArtifacts:
    def __init__(self,session,retained,reader):
        self._session=session;self._retained=MappingProxyType(dict(retained));self._reader=reader
        self._snapshot_keys=None;self._snapshot_text={}
        self._validate()
    @property
    def namespace(self):
        self._session._check();return self._reader.namespace
    def _validate(self):
        self._session._check()
        reusable=getattr(self._session,'_read_snapshot_reusable',lambda:False)()
        if reusable and self._snapshot_keys is not None:return self._snapshot_keys
        keys=self._reader.list_keys()
        if set(keys)&self._retained.keys():raise ValueError('Retained and continuation sources overlap; no precedence or repair')
        for key in keys:
            future_key(key)
            if key=='.campaigniq-monthly-publication-v1.json':validate_protocol(self._reader.read_text(key))
        if reusable:self._snapshot_keys=tuple(keys)
        return keys
    def list_keys(self,*,prefix='',suffix=''):
        keys=self._validate()
        return tuple(sorted(k for k in (*self._retained,*keys) if k.startswith(prefix) and k.endswith(suffix)))
    def exists(self,key):return key in self.list_keys()
    def read_text(self,key):
        self._validate()
        if key in self._retained:return self._retained[key]
        reusable=getattr(self._session,'_read_snapshot_reusable',lambda:False)()
        if reusable and key in self._snapshot_text:return self._snapshot_text[key]
        text=self._reader.read_text(key)
        if reusable:self._snapshot_text[key]=text
        return text
    def write_text(self,*args,**kwargs):raise PermissionError('Continuation sources are read-only outside publication')
    def delete(self,*args,**kwargs):raise PermissionError('Continuation sources cannot delete retained artifacts')
    def initialize(self):raise PermissionError('Continuation cannot initialize schemas')

class ContinuationDrafts:
    def __init__(self,session,reader):self._session=session;self._reader=reader
    @property
    def workspace(self):self._session._check();return self._reader.workspace
    def _check(self,draft):
        self._session._check();future_month(draft.year,draft.month);return draft
    def create_or_resume(self,year,month):
        self._session._check(write=True);future_month(year,month)
        return self._check(self._reader.create_or_resume(year,month))
    def get(self,draft_id):return self._check(self._reader.get(draft_id))
    def list_drafts(self):return tuple(self._check(draft) for draft in self._reader.list_drafts())
    def upload(self,draft_id,*args,**kwargs):
        self._session._check(write=True);self.get(draft_id)
        return self._reader.upload(draft_id,*args,**kwargs)
    def abandon(self,draft_id):
        self._session._check(write=True);self.get(draft_id);return self._reader.abandon(draft_id)
    def __getattr__(self,name):
        # Original reads and metadata also retain the underlying expiring session.
        if name.startswith('_'):raise AttributeError(name)
        return getattr(self._reader,name)

def attach_continuation(session,account):
    """Called only after current directory authorization and exact schema checks."""
    if not account.legacy:return
    scope=account.storage_scope
    expected=expected_months(scope)
    with session._operation() as conn:
        retained=conn.execute('SELECT artifact_key,content FROM public.artifact_storage_dev WHERE namespace=%s ORDER BY artifact_key',(scope,)).fetchall()
        source=ReportSnapshot(scope,retained)
        if published_months(source)!=expected:raise ValueError('Reviewed retained history frontier changed')
        for period in expected:
            year,month=map(int,period.split('-'))
            _results(source,account.id,year,month,[(k,v) for k,v in retained if k.startswith(period+'-')])
        # The inventory found empty future drafts. Never hide a newly uploaded
        # retained original or silently migrate a nonempty draft into new storage.
        pending=conn.execute('SELECT p.year,p.month,p.revision,count(d.id) FROM public.import_drafts p '
            'LEFT JOIN public.import_draft_documents d ON d.draft_id=p.id '
            "WHERE p.workspace=%s AND p.state='draft' AND (p.year,p.month)>(2026,9) "
            'GROUP BY p.id,p.year,p.month,p.revision ORDER BY p.year,p.month',(scope,)).fetchall()
        if any(revision!=0 or count!=0 for _,_,revision,count in pending):
            raise ValueError('Retained future draft now contains saved sources; reviewed resumption required')
    session.retained_rows=tuple(retained)
    session.artifacts=ContinuationArtifacts(session,retained,session.artifacts)
    session.drafts=ContinuationDrafts(session,session.drafts)
