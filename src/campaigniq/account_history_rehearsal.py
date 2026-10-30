"""LOCAL same-snapshot retained trade-history verification for new synthetic accounts."""
import argparse
from datetime import datetime, timezone, date
from hashlib import sha256
from tempfile import TemporaryDirectory
from campaigniq.account_legacy_access_rehearsal import protected_rows, seed_sources
from campaigniq.account_legacy_results_rehearsal import _candidate
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.persistence.account_directory_codec import checksum
from decimal import Decimal
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from campaigniq.account_administration import Identity, LegacyOwner, Permission, propose_legacy_directory
from campaigniq.account_directory_rehearsal import must_fail
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.account_signedin_inputs import SignedInMonthlyInputs
from campaigniq.account_signedin_inputs_rehearsal import input_state
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.persistence.signedin_account_data import DATA_SCHEMA, LocalSignedInInputGateway


def _state(store,scopes,*,exclude_own=True):
    result=input_state(store,exclude_own=exclude_own)
    scoped=protected_rows(store,scopes if exclude_own else (),exclude_registry=exclude_own)
    for table in ('artifact_storage_dev','import_drafts','import_draft_documents'):
        key='public.'+table
        result.pop(key,None)
        if key in scoped:result[key]=scoped[key]
    return result


def run_rehearsal(output, dsn='dbname=campaigniq_dev'):
    import psycopg
    registry = str(uuid4())
    store = LocalPostgresAccountDirectory(dsn, registry, development_only=True)
    issuer = 'https://account-history-rehearsal.invalid'
    admin, member = Identity(issuer,registry+'-a'), Identity(issuer,registry+'-b')
    scopes = ('workspace-history-'+registry+'-a','workspace-history-'+registry+'-b')
    initial = propose_legacy_directory(tuple(LegacyOwner(identity,scope,'Synthetic owner','Retained account','schwab')
        for identity,scope in zip((admin,member),scopes)),admin)
    gateway = LocalSignedInInputGateway(store,trusted_issuer=issuer,
        authorization={admin.subject:scopes[0],member.subject:scopes[1]})
    service = SignedInMonthlyInputs(gateway)
    sa,sb = RehearsalSession(admin),RehearsalSession(member)
    before = _state(store,scopes)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report = dict(format='campaigniq.account_history_rehearsal',version=1,registry_id=registry,
        checks={},production_connection_made=False,production_publication_authorized=False)
    def passed(name):
        report['checks'][name]=True
        print(name.replace('_',' ')+': PASS',flush=True)
    def edit(action,/,**values):
        return store.execute(admin,store._snapshot().revision,action,**values)
    try:
        store.initialize();store.bootstrap(initial)
        for name in ('Synthetic A','Synthetic B'):
            edit('add_account',owner_id=initial.users[0].id,name=name,broker='schwab')
        a,b = (account.id for account in store._snapshot().accounts if not account.legacy)
        gateway.initialize(sa)
        gateway.bind(sa,a,'securities','12345678',expected_revision=store._snapshot().revision)
        da,db = service.create(sa,a,2025,1),service.create(sa,b,2025,1)
        sources = synthetic_sources('12345678')
        revision=0
        for role,upload in sources.items():revision=service.save(sa,a,da.id,revision,role,upload)
        from campaigniq import account_signedin_results as module
        service.publish(sa,a,da.id,revision,authorize_local_publication=True,forex_applicable=False,crypto_applicable=False)
        gateway.bind(sa,b,'securities','87654321',expected_revision=store._snapshot().revision)
        second_revision=0
        for role,upload in synthetic_sources('87654321',double=True).items():
            second_revision=service.save(sa,b,db.id,second_revision,role,upload)
        service.publish(sa,b,db.id,second_revision,authorize_local_publication=True,forex_applicable=False,crypto_applicable=False)
        from campaigniq.account_dashboard import LocalAccountDashboard
        from campaigniq import account_history_evidence as history_module
        from campaigniq.account_history_evidence import materialize_history
        from campaigniq.ui.position_history_view import _source_matches_manifest, _raw_entries
        dashboard=LocalAccountDashboard(store,trusted_issuer=issuer,
            authorization={admin.subject:scopes[0],member.subject:scopes[1]})
        def read(user=sa,target=a):return dashboard.snapshot(user,target,include_history=True)
        observed=[];actual=history_module.retained_history_rows
        def checked(conn,scope,*,legacy):
            observed.append((conn.execute('SHOW transaction_read_only').fetchone()[0],
                conn.execute('SHOW transaction_isolation').fetchone()[0]))
            return actual(conn,scope,legacy=legacy)
        current=_state(store,scopes,exclude_own=False)
        with patch.object(history_module,'retained_history_rows',side_effect=checked):
            first,second=read(),read(target=b)
        assert observed==[('on','repeatable read')]*2
        assert len(first.history.originals)==len(second.history.originals)==1
        assert first.history.originals[0].sha256!=second.history.originals[0].sha256
        assert not first.history.gaps and not second.history.gaps
        assert _state(store,scopes,exclude_own=False)==current
        passed('new_account_originals_share_the_readonly_repeatable_financial_snapshot')
        seed_sources(store,scopes)
        legacy_a=next(x.id for x in initial.accounts if x.owner_id==initial.user(admin).id)
        legacy_b=next(x.id for x in initial.accounts if x.owner_id==initial.user(member).id)
        ids=[];raws=[]
        for scope,number,double in ((scopes[0],'12345678',False),(scopes[1],'87654321',True)):
            raw=synthetic_sources(number,double=double)[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY].getvalue()
            raws.append(raw);identifier=uuid4();ids.append(identifier);draft=uuid4()
            with store._connection() as conn:
                for key,text in _candidate(number,double=double).items():
                    conn.execute('INSERT INTO public.artifact_storage_dev(namespace,artifact_key,content) VALUES(%s,%s,%s)',(scope,key,text))
                conn.execute("INSERT INTO public.import_drafts(id,workspace,year,month,state,revision) VALUES(%s,%s,2025,1,'abandoned',1)",(draft,scope))
                conn.execute("INSERT INTO public.import_draft_documents(id,draft_id,role,filename,media_type,sha256,content,revision,superseded) VALUES(%s,%s,%s,%s,'text/csv',%s,%s,1,true)",
                    (identifier,draft,MonthlyInputRole.THINKORSWIM_TRADE_HISTORY.value,'../../must-not-be-used.csv',sha256(raw).hexdigest(),raw))
        retained=read(target=legacy_a)
        assert len(retained.history.originals)==1 and retained.history.originals[0].content==raws[0]
        with TemporaryDirectory(prefix='campaigniq-history-readonly-') as folder:
            path=materialize_history(Path(folder)/'history',retained.history)
            assert _source_matches_manifest(retained,path,date(2025,1,1),date(2025,1,31))
            entries=_raw_entries(path,date(2025,1,1),date(2025,1,31))
            assert len(entries)==1 and Decimal(entries[0]['quantity_change'])==10
            assert not (Path(folder)/'must-not-be-used.csv').exists()
        assert not path.exists()
        passed('superseded_original_from_abandoned_draft_uses_canonical_private_filename_and_is_cleaned_up')
        # Add a matching copy only to the OTHER legacy workspace. Removing the
        # selected workspace's matching digest must not discover that copy.
        with store._connection() as conn:
            parent=conn.execute('SELECT draft_id FROM public.import_draft_documents WHERE id=%s',(ids[1],)).fetchone()[0]
            conn.execute("INSERT INTO public.import_draft_documents(id,draft_id,role,filename,media_type,sha256,content,revision,superseded) VALUES(%s,%s,%s,'foreign-copy.csv','text/csv',%s,%s,2,true)",
                (uuid4(),parent,MonthlyInputRole.THINKORSWIM_TRADE_HISTORY.value,sha256(raws[0]).hexdigest(),raws[0]))
            conn.execute('UPDATE public.import_draft_documents SET sha256=%s WHERE id=%s',('0'*64,ids[0]))
        gap=read(target=legacy_a)
        assert not gap.history.originals and len(gap.history.gaps)==1
        assert dict(gap._rows)==dict(retained._rows)
        with store._connection() as conn:
            conn.execute('UPDATE public.import_draft_documents SET sha256=%s WHERE id=%s',(sha256(raws[0]).hexdigest(),ids[0]))
        passed('matching_foreign_original_cannot_fill_selected_account_history_gap')
        with store._connection() as conn:
            conn.execute('UPDATE public.import_draft_documents SET content=%s WHERE id=%s',(raws[0]+b'changed',ids[0]))
        current=_state(store,scopes,exclude_own=False)
        gap=read(target=legacy_a)
        assert not gap.history.originals and dict(gap._rows)==dict(retained._rows)
        assert _state(store,scopes,exclude_own=False)==current
        with store._connection() as conn:
            conn.execute('UPDATE public.import_draft_documents SET content=%s WHERE id=%s',(raws[0],ids[0]))
        passed('corrupt_original_withholds_supplementary_history_without_repair_or_financial_changes')
        gateway.bind(sa,a,'securities','34567890',expected_revision=store._snapshot().revision,confirm_replace=True)
        gap=read()
        assert not gap.history.originals and len(gap.history.gaps)==1
        gateway.bind(sa,a,'securities','12345678',expected_revision=store._snapshot().revision,confirm_replace=True)
        assert len(read().history.originals)==1
        passed('new_account_history_rechecks_current_broker_binding')
        must_fail(PermissionError,lambda:read(target=legacy_b))
        edit('set_grant',user_id=initial.user(member).id,account_id=a,permission=Permission.VIEW)
        assert len(read(user=sb).history.originals)==1
        edit('set_grant',user_id=initial.user(member).id,account_id=a,permission=None)
        must_fail(PermissionError,lambda:read(user=sb))
        edit('configure_account',account_id=a,archived=True)
        assert len(read().history.originals)==1
        edit('configure_user',user_id=initial.user(member).id,enabled=False)
        must_fail(PermissionError,lambda:read(user=sb,target=legacy_b))
        passed('current_signin_view_grants_revocation_archive_and_disabled_access_rechecked')
        current=_state(store,scopes,exclude_own=False)
        def write(conn,scope,*,legacy):
            conn.execute('UPDATE public.artifact_storage_dev SET content=content WHERE false')
        with patch.object(history_module,'retained_history_rows',side_effect=write):
            must_fail(psycopg.errors.ReadOnlySqlTransaction,lambda:read(target=legacy_a))
        assert _state(store,scopes,exclude_own=False)==current
        passed('injected_raw_sql_write_is_rejected_and_all_rows_remain_unchanged')
        assert _state(store,scopes)==before
        passed('all_existing_financial_records_originals_directory_and_prior_test_accounts_unchanged')
        report['local_rehearsal_passed']=True
    except Exception as error:
        report.update(local_rehearsal_passed=False,error_type=type(error).__name__)
        raise
    finally:
        (output/'account-history-report.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    print('Report:',output/'account-history-report.json')
    print('Only NEW synthetic local sources added. Existing accounts and production unchanged.')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--development-only',action='store_true',required=True);p.parse_args()
    output=Path.home()/'CampaignIQ-Records/Development-Rehearsals'/('Account-history-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
    run_rehearsal(output,os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL','dbname=campaigniq_dev'))

if __name__=='__main__':main()
