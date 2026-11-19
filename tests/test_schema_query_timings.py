"""Exact catalog SQL/results/fingerprints remain stable with fixed timing labels."""
import pytest
from campaigniq import account_schema_migrations as schema,account_runtime_roles as roles,navigation_timing as timing
REFERENCE_SQL=('SELECT nspname,pg_get_userbyid(nspowner),nspacl::text FROM pg_namespace WHERE nspname=%s', 'SELECT c.relname,c.relkind,pg_get_userbyid(c.relowner),c.relacl::text,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname', 'SELECT c.relname,a.attname,a.attnum,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum', 'SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname,k.conname', 'SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=%s ORDER BY indexname', 'SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND NOT t.tgisinternal ORDER BY c.relname,t.tgname', 'SELECT p.proname,pg_get_function_identity_arguments(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s ORDER BY 1,2', 'SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname=%s ORDER BY tablename,policyname')
REFERENCE_GRANTS=["SELECT nspname,nspacl::text FROM pg_namespace WHERE nspname IN ('public','campaigniq_accounts') ORDER BY 1", "SELECT n.nspname,c.relname,c.relacl::text,a.attname,a.attacl::text\n        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace\n        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped\n        WHERE n.nspname='campaigniq_accounts' OR (n.nspname='public'\n          AND c.relname IN ('artifact_storage_dev','import_drafts','import_draft_documents'))\n        ORDER BY 1,2,a.attnum"]
LABELS=('catalog_namespace','catalog_relations','catalog_columns','catalog_constraints','catalog_indexes','catalog_triggers','catalog_functions','catalog_policies')
class Conn:
    def __init__(self,fail=None):self.calls=[];self.fail=fail
    def execute(self,sql,args=None):
        self.calls.append((sql,args));n=len(self.calls)
        if n==self.fail:raise PermissionError('catalog failure')
        class Rows:
            def fetchall(self):return [(n,None,['original','order'])]
        return Rows()

def test_all_catalog_sql_parameters_order_rows_and_fingerprint_unchanged():
    conn=Conn();trace=timing.Trace(False);token=timing._current.set(trace)
    try:
        with timing.connection_timing_scope('startup_reader_'):actual=schema.catalog_digest(conn)
    finally:timing._current.reset(token)
    assert conn.calls==[(q,(schema.SCHEMA,)) for q in REFERENCE_SQL]
    assert actual==schema.digest([[(i,None,['original','order'])] for i in range(1,9)])
    assert set(trace.stages)=={'startup_reader_'+label for label in LABELS+('catalog_fingerprint',)}
    assert all(v['calls']==1 for v in trace.stages.values())

@pytest.mark.parametrize('number',range(1,9))
def test_failed_catalog_query_propagates_without_reading_later_rows(number):
    conn=Conn(fail=number);trace=timing.Trace(False);token=timing._current.set(trace)
    try:
        with pytest.raises(PermissionError):
            with timing.connection_timing_scope('startup_publisher_'):schema.catalog_digest(conn)
    finally:timing._current.reset(token)
    assert len(conn.calls)==number and trace.active==[]
    assert 'startup_publisher_catalog_fingerprint' not in trace.stages
    assert timing._connection_prefix.get() is None

def test_grant_sql_and_fingerprint_unchanged():
    conn=Conn();trace=timing.Trace(False);token=timing._current.set(trace)
    try:
        with timing.connection_timing_scope('startup_preferences_'):actual=roles.role_catalog_digest(conn,base_catalog='a'*64)
    finally:timing._current.reset(token)
    assert conn.calls==[(q,None) for q in REFERENCE_GRANTS]
    assert actual==schema.digest(('a'*64,[(1,None,['original','order'])],[(2,None,['original','order'])]))
    assert set(trace.stages)=={'startup_preferences_'+s for s in ('grant_schemas','grant_relations','grant_fingerprint')}

def test_unscoped_catalog_adds_no_trace_labels():
    trace=timing.Trace(False);token=timing._current.set(trace)
    try:schema.catalog_digest(Conn())
    finally:timing._current.reset(token)
    assert trace.stages=={}

def test_new_labels_pass_existing_sanitized_performance_export():
    from collections import deque
    from unittest.mock import patch
    from campaigniq import performance_monitor as monitor
    names={'startup_'+role+'_'+phase for role in ('reader','publisher','preferences') for phase in timing._SCHEMA_PHASES}
    with patch.object(monitor,'_events',deque(maxlen=2000)),patch.object(monitor._logger,'info'):
        monitor.record('page','Data',1,stages={k:dict(calls=1,seconds=.001) for k in names})
        report=monitor.snapshot(administrator=True)
    assert set(report['recent_events'][0]['stages'])==names
