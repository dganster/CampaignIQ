"""Bounded process-local server timings and sanitized structured service logs."""
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import json
import logging
from math import ceil
import sys
from threading import Lock
from time import perf_counter

_VIEWS=frozenset(('Overview','Campaigns','Performance','Positions','Data','Crypto','Compare accounts','Administration'))
_OPERATIONS=frozenset(('monthly_source_preview','no_trading_preview','save_no_trading_confirmation','publish','save','save_associated_gain_loss','results','reconcile','preview','futures_preview','futures_monthly_preview','saved_documents','draft_recovery_preview','recover_draft','review_forex_cash_sources','save_forex_cash_sources','saved_forex_cash_source_preview','review_crypto_statements','save_crypto_statement_sources','crypto_statement_preview','crypto_generation_preview','save_futures_statement'))
_events=deque(maxlen=2000)
_lock=Lock()
_logger=logging.getLogger('campaigniq.performance')
_logger.setLevel(logging.INFO)
_logger.propagate=False
if not _logger.handlers:
    _handler=logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter('%(message)s'))
    _logger.addHandler(_handler)

def record(kind,name,seconds,*,outcome='success',stages=None,profiled=False):
    if (kind=='page' and name not in _VIEWS|{'Startup'}) or (kind=='operation' and name not in _OPERATIONS) or kind not in ('page','operation'):return
    if outcome not in ('success','error','rerun','stopped'):return
    event=dict(event='campaigniq.performance.v1',timestamp=datetime.now(timezone.utc).isoformat(),kind=kind,name=name,
        seconds=round(max(0.0,seconds),6),outcome=outcome,profiled=profiled is True)
    if stages is not None:
        from campaigniq.navigation_timing import _STAGES
        event['stages']={k:dict(calls=int(v['calls']),seconds=round(max(0.0,v['seconds']),6)) for k,v in stages.items() if k in _STAGES}
    with _lock:_events.append(event)
    # Monitoring must never turn a completed financial action into a reported failure.
    try:_logger.info(json.dumps(event,separators=(',',':')))
    except Exception:pass

@contextmanager
def operation(name):
    start=perf_counter();outcome='success'
    try:yield
    except BaseException:
        outcome='error';raise
    finally:record('operation',name,perf_counter()-start,outcome=outcome)

class TimedService:
    """Forward unchanged arguments/results and permissions to the real service."""
    def __init__(self,service):self._service=service
    def __getattr__(self,name):
        value=getattr(self._service,name)
        if name not in _OPERATIONS or not callable(value):return value
        @wraps(value)
        def measured(*args,**kwargs):
            with operation(name):return value(*args,**kwargs)
        return measured

def snapshot(*,administrator):
    if administrator is not True:raise PermissionError('Administrator required')
    with _lock:events=list(_events)
    grouped={}
    for e in events:grouped.setdefault((e['kind'],e['name'],e['profiled']),[]).append(e)
    rows=[]
    for (kind,name,profiled),items in sorted(grouped.items()):
        values=sorted(e['seconds'] for e in items)
        rows.append(dict(kind=kind,name=name,profiled=profiled,count=len(items),errors=sum(e['outcome']=='error' for e in items),
            p50_seconds=values[ceil(len(values)*.5)-1],p95_seconds=values[ceil(len(values)*.95)-1],max_seconds=values[-1],
            last_seconds=items[-1]['seconds']))
    phases={}
    for e in events:
        for name,value in e.get('stages',{}).items():
            phases.setdefault((e['name'],name,e['profiled']),[]).append(value['seconds'])
    phase_rows=[]
    for (view,name,profiled),values in sorted(phases.items()):
        values.sort()
        phase_rows.append(dict(view=view,stage=name,profiled=profiled,count=len(values),p95_seconds=values[ceil(len(values)*.95)-1],max_seconds=values[-1]))
    return dict(stage_summary=phase_rows,scope='Server work only; excludes browser rendering and network delivery. Nested stage and operation times overlap. Last 2,000 events in this worker; resets on restart. Service logs contain timestamped events for longer monitoring.',
        events_retained=len(events),oldest_timestamp=events[0]['timestamp'] if events else None,summary=rows,recent_events=events[-50:])
