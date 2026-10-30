"""Deployment directory and bootstrap rehearsal on pinned LOCAL restore; rollback all."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import pwd
from unittest.mock import patch
from uuid import uuid4
from campaigniq.account_administration import Identity,LegacyOwner,Permission
from campaigniq.account_backend_config import AccountDirectorySettings
from campaigniq.account_directory_bootstrap import review_bootstrap,bootstrap_directory,legacy_fingerprint
from campaigniq.account_schema_migrations import apply_migrations,verify_schema,SCHEMA
from campaigniq.account_schema_rehearsal import restore_conninfo,inventory,DATABASE
from campaigniq.persistence.account_directory_codec import encode
from campaigniq.persistence.deployment_account_directory import DirectoryTransaction


class ServerSession:
    """Synthetic server session only for this offline rehearsal, never UI selection."""
    def __init__(self,identity,*,logged_in=True):self.identity=identity;self.logged_in=logged_in
    def to_dict(self):return dict(is_logged_in=self.logged_in,iss=self.identity.issuer,sub=self.identity.subject)


def rejected(kind,call):
    try:call()
    except kind:return
    raise AssertionError('Expected rejection: '+kind.__name__)


def run_rehearsal(output,*,development_only):
    import psycopg
    conninfo=restore_conninfo(development_only=development_only)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report=dict(format='campaigniq.account_backend_rehearsal',version=1,checks={},database=DATABASE,
                production_connection_made=False,account_mode_activated=False)
    def passed(name):
        report['checks'][name]=True;print(name.replace('_',' ')+': PASS',flush=True)
    try:
        with psycopg.connect(conninfo,autocommit=True) as conn:
            if conn.execute('SELECT current_database(),current_user,inet_server_addr()').fetchone()!=(DATABASE,pwd.getpwuid(os.getuid()).pw_name,None):
                raise ValueError('Unexpected database target')
            if conn.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(SCHEMA,)).fetchone():
                raise ValueError('Restore already has deployment schema; no replacement attempted')
            with conn.transaction(force_rollback=True):
                conn.execute("SET LOCAL lock_timeout='5s'")
                conn.execute("SET LOCAL statement_timeout='30s'")
                before=inventory(conn,lock=True)
                scopes=tuple(row[0] for row in conn.execute('''SELECT namespace FROM public.artifact_storage_dev
                    WHERE namespace LIKE 'workspace-%' UNION SELECT workspace FROM public.import_drafts
                    WHERE workspace LIKE 'workspace-%' ORDER BY 1''').fetchall())
                if len(scopes)<2:raise ValueError('Two restored owner namespaces required for isolation rehearsal')
                issuer='https://deployment-directory-rehearsal.invalid'
                identities=tuple(Identity(issuer,'synthetic-owner-'+str(i)) for i in range(len(scopes)))
                settings=AccountDirectorySettings(str(uuid4()),issuer,tuple(sorted((u.subject,w) for u,w in zip(identities,scopes))))
                owners=tuple(LegacyOwner(u,w,'Synthetic owner '+str(i),'Retained brokerage account','schwab')
                    for i,(u,w) in enumerate(zip(identities,scopes)))
                sa,sb=map(ServerSession,identities[:2])
                legacy=legacy_fingerprint(conn,settings)
                plan=review_bootstrap(conn,settings,owners,identities[0])
                assert review_bootstrap(conn,settings,tuple(reversed(owners)),identities[0])==plan
                assert inventory(conn)==before
                passed('explicit_owner_mapping_proposal_is_deterministic_and_readonly')
                apply_migrations(conn);verify_schema(conn)
                try:
                    with conn.transaction():
                        with patch.object(DirectoryTransaction,'_load',side_effect=RuntimeError('injected bootstrap failure')):
                            bootstrap_directory(conn,settings,plan,reviewed_sha256=plan.sha256)
                except RuntimeError as error:
                    if str(error)!='injected bootstrap failure':raise
                else:raise AssertionError('Injection did not fail')
                assert conn.execute('SELECT count(*) FROM campaigniq_accounts.directories').fetchone()[0]==0
                passed('injected_bootstrap_failure_rolls_back_directory_metadata')
                rejected(ValueError,lambda:bootstrap_directory(conn,settings,plan,reviewed_sha256='0'*64))
                assert bootstrap_directory(conn,settings,plan,reviewed_sha256=plan.sha256) is True
                assert bootstrap_directory(conn,settings,plan,reviewed_sha256=plan.sha256) is False
                tx=DirectoryTransaction(conn,settings)
                assert tx._load()==plan.directory and legacy_fingerprint(conn,settings)==legacy
                passed('reviewed_bootstrap_is_idempotent_and_preserves_exact_legacy_bindings')
                a,b=(next(a for a in tx._load().accounts if a.owner_id==u.id).id for u in plan.directory.users[:2])
                assert tx.authorize(sa,a).storage_scope==scopes[0]
                assert tx.authorize(sb,b).storage_scope==scopes[1]
                rejected(PermissionError,lambda:tx.authorize(sa,b))
                rejected(PermissionError,lambda:tx.snapshot(sb))
                rejected(PermissionError,lambda:tx.session({'iss':issuer,'sub':identities[0].subject,'is_logged_in':True}))
                rejected(PermissionError,lambda:tx.session(ServerSession(identities[0],logged_in=False)))
                rejected(PermissionError,lambda:tx.session(ServerSession(Identity('https://wrong-issuer.invalid',identities[0].subject))))
                passed('server_signin_and_owner_isolation_hold_admin_has_no_implicit_financial_access')
                def command(action,**values):return tx.execute(sa,tx._load().revision,action,**values)
                command('configure_account',account_id=a,name='Reviewed local rename')
                state=tx._load()
                assert state.events[-1].actor_id==state.user(identities[0]).id
                rejected(ValueError,lambda:tx.execute(sa,0,'configure_account',account_id=a,name='Stale rename'))
                rejected(PermissionError,lambda:tx.execute(sb,state.revision,'configure_account',account_id=b,name='Unauthorized'))
                rejected(ValueError,lambda:command('add_user',identity=Identity('https://wrong-issuer.invalid','foreign'),name='Foreign'))
                assert tx._load()==state
                passed('commands_attribute_current_admin_and_reject_stale_unauthorized_and_wrong_issuer_changes')
                member=state.user(identities[1]).id
                command('set_grant',user_id=member,account_id=a,permission=Permission.VIEW)
                assert not tx.authorize(sb,a).writable
                rejected(PermissionError,lambda:tx.authorize(sb,a,write=True))
                command('set_grant',user_id=member,account_id=a,permission=None)
                rejected(PermissionError,lambda:tx.authorize(sb,a))
                command('configure_account',account_id=b,archived=True)
                assert not tx.authorize(sb,b).writable
                rejected(PermissionError,lambda:tx.authorize(sb,b,write=True))
                command('configure_user',user_id=member,enabled=False)
                rejected(PermissionError,lambda:tx.session(sb))
                assert tx.session(sa).administrator
                rejected(ValueError,lambda:command('configure_user',user_id=state.user(identities[0]).id,admin=False))
                passed('grants_revocation_archive_disable_and_last_admin_guard_rechecked')
                prior=tx._load()
                try:
                    with conn.transaction():
                        with patch.object(tx,'_append_event',side_effect=RuntimeError('injected audit failure')):
                            command('configure_account',account_id=a,name='Must roll back')
                except RuntimeError as error:
                    if str(error)!='injected audit failure':raise
                else:raise AssertionError('Injection did not fail')
                assert tx._load()==prior
                passed('directory_update_and_audit_roll_back_together_after_injected_failure')
                with conn.transaction(force_rollback=True):
                    conn.execute("UPDATE campaigniq_accounts.directories SET document_sha256=repeat('0',64)")
                    rejected(ValueError,lambda:tx.session(sa))
                with conn.transaction(force_rollback=True):
                    conn.execute("UPDATE campaigniq_accounts.audit_events SET event_sha256=repeat('0',64) WHERE sequence=1")
                    rejected(ValueError,lambda:tx.session(sa))
                changed=AccountDirectorySettings(settings.registry_id,'https://changed-issuer.invalid',settings.legacy_authorization)
                rejected(ValueError,lambda:DirectoryTransaction(conn,changed)._load())
                assert tx._load()==prior
                passed('directory_audit_and_trusted_mapping_conflicts_block_access_without_repair')
                after=inventory(conn)
                assert {k:v for k,v in after.items() if not k.startswith(SCHEMA+'.')}==before
                assert legacy_fingerprint(conn,settings)==legacy
                for table in ('artifact_storage_dev','import_drafts','import_draft_documents','account_preferences'):
                    assert conn.execute('SELECT count(*) FROM campaigniq_accounts.'+table).fetchone()[0]==0
                passed('all_existing_financial_rows_originals_preferences_and_prior_metadata_unchanged')
        with psycopg.connect(conninfo) as observer:
            observer.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            assert not observer.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(SCHEMA,)).fetchone()
            assert inventory(observer)==before
        passed('independent_reader_confirms_full_restore_and_schema_rollback')
        report.update(passed=True,reviewed_bootstrap_sha256=plan.sha256)
    except Exception as error:
        report.update(passed=False,error_type=type(error).__name__);raise
    finally:
        with (output/'account-backend-report.json').open('x') as handle:
            json.dump(report,handle,indent=2,sort_keys=True);handle.write('\n');handle.flush();os.fsync(handle.fileno())
        print('Report:',output/'account-backend-report.json')
    print('Local full restore unchanged. Synthetic bootstrap and all commands rolled back. Production unchanged.')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only',action='store_true',required=True)
    args=parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    run_rehearsal(Path.home()/'CampaignIQ-Records/Development-Rehearsals'/('Account-backend-'+stamp),development_only=args.development_only)


if __name__=='__main__':main()
