"""Exercise separate personal preferences using NEW synthetic LOCAL metadata."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from campaigniq.account_administration import Identity,LegacyOwner,Permission,propose_legacy_directory
from campaigniq.account_directory_rehearsal import must_fail
from campaigniq.account_legacy_access_rehearsal import RehearsalSession,protected_rows
from campaigniq.account_preferences import LocalAccountPreferences,TABLE
from campaigniq.persistence.account_directory_codec import checksum
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.ui.preferences import DEFAULT_PREFERENCES,preference_key
from campaigniq.ui.account_preferences_view import PreferenceStorage
from campaigniq.account_dashboard import ReportSnapshot


def state(store,*,exclude_own=True):
    result=protected_rows(store,(),exclude_registry=exclude_own)
    result.pop(TABLE,None)
    with store._connection(readonly=True) as conn:
        conn.execute("SET LOCAL timezone='UTC'")
        if conn.execute('SELECT to_regclass(%s)',(TABLE,)).fetchone()[0] is not None:
            where=' WHERE registry_id<>%s' if exclude_own else ''
            rows=conn.execute('SELECT md5(row_to_json(t)::text),count(*) FROM '+TABLE+' t'+where+' GROUP BY 1 ORDER BY 1',
                (store.registry_id,) if exclude_own else None).fetchall()
            if rows:result[TABLE]=checksum(rows)
    return result


def run_rehearsal(output,dsn='dbname=campaigniq_dev'):
    import psycopg
    registry=str(uuid4());issuer='https://personal-preferences-rehearsal.invalid'
    admin,member=(Identity(issuer,registry+suffix) for suffix in ('-a','-b'))
    scopes=('workspace-preferences-'+registry+'-a','workspace-preferences-'+registry+'-b')
    initial=propose_legacy_directory(tuple(LegacyOwner(i,s,'Synthetic owner','Retained account','schwab')
        for i,s in zip((admin,member),scopes)),admin)
    mapping=dict(zip((admin.subject,member.subject),scopes))
    store=LocalPostgresAccountDirectory(dsn,registry,development_only=True)
    service=LocalAccountPreferences(store,trusted_issuer=issuer,authorization=mapping)
    sa,sb=RehearsalSession(admin),RehearsalSession(member)
    before=state(store)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report=dict(format='campaigniq.account_preferences_rehearsal',version=1,registry_id=registry,
        checks={},production_connection_made=False)
    def passed(name):report['checks'][name]=True;print(name.replace('_',' ')+': PASS',flush=True)
    def edit(command,**values):return store.execute(admin,store._snapshot().revision,command,**values)
    try:
        store.initialize();store.bootstrap(initial)
        a=next(x.id for x in initial.accounts if x.owner_id==initial.user(admin).id)
        b=next(x.id for x in initial.accounts if x.owner_id==initial.user(member).id)
        must_fail(PermissionError,lambda:service.initialize(sb))
        service.initialize(sa)
        values={**DEFAULT_PREFERENCES,'fit_columns':True,'timezone':'America/Denver'}
        assert service.save(sa,a,values,expected_revision=0)==1
        independent=LocalAccountPreferences(store,trusted_issuer=issuer,authorization=mapping)
        assert dict(independent.load(sa,a).values)==values
        must_fail(PermissionError,lambda:service.load(sa,b))
        edit('set_grant',user_id=initial.user(member).id,account_id=a,permission=Permission.VIEW)
        assert not service.load(sb,a).values
        service.save(sb,a,dict(DEFAULT_PREFERENCES),expected_revision=0)
        assert dict(service.load(sa,a).values)==values
        passed('independent_connections_preserve_personal_choices_and_account_user_isolation')
        key=preference_key(admin.subject);raw=json.dumps({'version':1,**DEFAULT_PREFERENCES})
        snapshot=ReportSnapshot(scopes[0],[(key,raw)])
        adapter=PreferenceStorage(service,sa,a,admin.subject,snapshot)
        assert json.loads(adapter.read_text(key))['fit_columns'] is True
        adapter.write_text(key,json.dumps({'version':1,**values}))
        assert snapshot.read_text(key)==raw
        passed('preference_adapter_preserves_retained_financial_artifact_bytes')
        current=state(store,exclude_own=False)
        must_fail(ValueError,lambda:service.save(sa,a,values,expected_revision=0))
        must_fail(PermissionError,lambda:service.save(admin,a,values,expected_revision=2))
        assert state(store,exclude_own=False)==current
        passed('stale_revision_and_forged_session_leave_all_rows_unchanged')
        edit('set_grant',user_id=initial.user(member).id,account_id=a,permission=None)
        must_fail(PermissionError,lambda:service.save(sb,a,values,expected_revision=1))
        edit('configure_account',account_id=a,archived=True)
        assert dict(service.load(sa,a).values)==values
        service.save(sa,a,values,expected_revision=2)
        edit('configure_user',user_id=initial.user(member).id,enabled=False)
        must_fail(PermissionError,lambda:service.load(sb,b))
        passed('current_grants_revocation_archived_display_choices_and_disabled_user_rechecked')
        edit('add_account',owner_id=initial.user(admin).id,name='Synthetic preference account',broker='schwab')
        c=next(x.id for x in store._snapshot().accounts if not x.legacy)
        def save_one():
            try:service.save(sa,c,values,expected_revision=0);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(lambda _:save_one(),range(2)))
        assert outcomes.count(True)==1 and service.load(sa,c).revision==1
        assert service.load(sa,a).revision==3 and dict(service.load(sa,a).values)==values
        passed('concurrent_first_save_has_one_winner_without_lost_updates')
        current=state(store,exclude_own=False);actual=service._read;calls=[]
        def fail(conn,key):
            calls.append(key)
            if len(calls)==2:raise ValueError('Injected post-write verification failure')
            return actual(conn,key)
        with patch.object(service,'_read',side_effect=fail):
            must_fail(ValueError,lambda:service.save(sa,c,values,expected_revision=1))
        assert state(store,exclude_own=False)==current
        def raw_write(conn,key):
            conn.execute('UPDATE '+TABLE+' SET revision=revision WHERE registry_id=%s',(registry,))
        with patch.object(service,'_read',side_effect=raw_write):
            must_fail(psycopg.errors.ReadOnlySqlTransaction,lambda:service.load(sa,c))
        assert state(store,exclude_own=False)==current
        passed('injected_post_write_failure_rolls_back_and_loads_reject_raw_sql_writes')
        own_key=(registry,initial.user(admin).id,c)
        with store._connection() as conn:
            saved_hash=conn.execute('SELECT document_sha256 FROM '+TABLE+' WHERE registry_id=%s AND user_id=%s AND account_id=%s',own_key).fetchone()[0]
            conn.execute('UPDATE '+TABLE+' SET document_sha256=%s WHERE registry_id=%s AND user_id=%s AND account_id=%s',('0'*64,*own_key))
        corrupted=state(store,exclude_own=False)
        must_fail(ValueError,lambda:service.load(sa,c))
        must_fail(ValueError,lambda:service.save(sa,c,values,expected_revision=1))
        assert state(store,exclude_own=False)==corrupted
        with store._connection() as conn:
            conn.execute('UPDATE '+TABLE+' SET document_sha256=%s WHERE registry_id=%s AND user_id=%s AND account_id=%s',(saved_hash,*own_key))
        assert dict(service.load(sa,c).values)==values
        passed('corrupt_personal_preference_rows_cannot_be_overwritten')
        assert state(store)==before
        passed('existing_financial_tables_original_documents_directory_and_prior_preferences_unchanged')
        report['local_rehearsal_passed']=True
    except Exception as error:
        report.update(local_rehearsal_passed=False,error_type=type(error).__name__)
        raise
    finally:
        (output/'account-preferences-report.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    print('Report:',output/'account-preferences-report.json')
    print('Only NEW synthetic local preference and directory metadata added. Production unchanged.')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--development-only',action='store_true',required=True);p.parse_args()
    output=Path.home()/'CampaignIQ-Records/Development-Rehearsals'/('Account-preferences-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
    run_rehearsal(output,os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL','dbname=campaigniq_dev'))

if __name__=='__main__':main()
