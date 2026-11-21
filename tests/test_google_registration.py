from types import SimpleNamespace
import pytest
from campaigniq.account_administration import Identity, LegacyOwner, propose_legacy_directory
from campaigniq.account_google_registration import request_registration, open_invitations
from campaigniq.persistence.account_directory_codec import encode, decode
ISSUER='https://accounts.google.com'
def directory():
    actor=Identity(ISSUER,'owner')
    d=propose_legacy_directory((LegacyOwner(actor,'workspace-owner','Dennis','Dennis account','schwab'),),actor)
    return d.invite_google_user(actor,'Matthew.Test@gmail.com','Matthew',expected_revision=0)
def session(**changes):
    claims=dict(is_logged_in=True,iss=ISSUER,sub='matthew',email='matthew.test@gmail.com',email_verified=True)
    claims.update(changes)
    return SimpleNamespace(to_dict=lambda:claims)
def test_approval_separate_from_brokerage_access_and_repeat_is_idempotent():
    before=directory();d=request_registration(before,session(),trusted_issuer=ISSUER);u=d.users[-1]
    assert not u.enabled and not u.admin and d.accounts==before.accounts and d.grants==before.grants
    assert d.events[:-1]==before.events and d.events[-1].actor_id==u.id and decode(encode(d))==d
    assert not open_invitations(d) and request_registration(d,session(),trusted_issuer=ISSUER)==d
    with pytest.raises(PermissionError):d.user(u.identity)
    approved=d.configure_user(d.users[0].identity,u.id,enabled=True,admin=False,expected_revision=d.revision)
    assert approved.user(u.identity).enabled
    with pytest.raises(PermissionError):approved.authorize(u.identity,d.accounts[0].id)
    with pytest.raises(PermissionError):request_registration(d,session(sub='different'),trusted_issuer=ISSUER)
@pytest.mark.parametrize('claims',[{'is_logged_in':False},{'iss':'https://other.test'},{'sub':''},
    {'email_verified':False},{'email_verified':'true'},{'email':'someone@gmail.com'},
    {'email':'matthewtest@gmail.com'},{'email':None}])
def test_reject_unverified_or_uninvited_claims(claims):
    with pytest.raises(PermissionError):request_registration(directory(),session(**claims),trusted_issuer=ISSUER)
def test_reject_raw_mapping_duplicate_invitation_and_stale_revision():
    d=directory()
    with pytest.raises(PermissionError):request_registration(d,session().to_dict(),trusted_issuer=ISSUER)
    with pytest.raises(ValueError):d.invite_google_user(d.users[0].identity,'matthew.test@gmail.com','Again',expected_revision=d.revision)
    with pytest.raises(ValueError):d.invite_google_user(d.users[0].identity,'new@gmail.com','New',expected_revision=0)

def test_disabled_inviter_and_replayed_request_cannot_enable_user():
    d=directory();other=Identity(ISSUER,'other-admin')
    d=d.add_user(d.users[0].identity,other,'Other',expected_revision=d.revision)
    d=d.configure_user(d.users[0].identity,d.users[-1].id,admin=True,expected_revision=d.revision)
    d=d.configure_user(other,d.users[0].id,enabled=False,expected_revision=d.revision)
    with pytest.raises(PermissionError):request_registration(d,session(),trusted_issuer=ISSUER)

def test_transaction_locks_and_does_not_persist_repeated_request(monkeypatch):
    from campaigniq.persistence.deployment_account_directory import DirectoryTransaction
    tx=object.__new__(DirectoryTransaction);tx.settings=SimpleNamespace(trusted_issuer=ISSUER)
    current=[directory()];locks=[];writes=[]
    def load(*,lock=False):locks.append(lock);return current[0]
    def persist(before,after):
        assert before==current[0];writes.append(after);current[0]=after
    tx._load=load;tx._persist_transition=persist
    assert 'pending' in tx.request_registration(session())
    assert 'pending' in tx.request_registration(session())
    assert locks==[True,True] and len(writes)==1

@pytest.mark.parametrize('deployment',[False,True])
def test_pending_registration_approval_screen_grants_no_account_access(deployment):
    from tests.test_account_signedin_admin_view import screen,button
    from campaigniq.ui.account_signedin_administration_view import directory_labels
    at=screen(deployment=deployment)
    # The UI fixture issuer is synthetic; construct the pending directory using
    # real verified-claim policy, then map its administrator to the fixture session.
    from dataclasses import replace
    pending=request_registration(directory(),session(),trusted_issuer=ISSUER)
    fixture=at.session_state['directory'];admin=fixture.users[0]
    pending_user=replace(pending.users[-1],identity=Identity(admin.identity.issuer,'matthew'))
    event=replace(pending.events[-1],sequence=1)
    at.session_state['directory']=replace(fixture,users=fixture.users+(pending_user,),events=(event,))
    at.run(timeout=15)
    assert not at.exception
    assert 'Approve Matthew' in [b.label for b in at.button]
    button(at,'Approve Matthew').click().run(timeout=15)
    assert not at.exception and not at.error
    updated=at.session_state['directory']
    assert updated.users[-1].enabled and not updated.users[-1].admin
    assert updated.accounts==fixture.accounts and updated.grants==fixture.grants
    assert directory_labels(updated)[1]=={a.id:a.name for a in updated.accounts}

def test_registration_screen_never_submits_on_render_and_handles_request():
    from streamlit.testing.v1 import AppTest
    code='''
import streamlit as st
from campaigniq.ui.account_dashboard_view import registration_screen
class Service:
    def request_registration(self,user):
        st.session_state.calls+=1
        return 'Registration is pending administrator approval.'
if 'calls' not in st.session_state:st.session_state.calls=0
registration_screen(st,Service())
'''
    at=AppTest.from_string(code).run(timeout=15)
    assert not at.exception and at.session_state['calls']==0
    next(b for b in at.button if b.label=='Request registration').click().run(timeout=15)
    assert not at.exception and at.session_state['calls']==1
    assert any('pending' in i.value for i in at.info)

def test_enrollment_persists_exact_audit_then_admin_approval_with_cas():
    from uuid import uuid4
    from campaigniq.account_backend_config import AccountDirectorySettings
    from campaigniq.persistence.deployment_account_directory import DirectoryTransaction
    from campaigniq.account_backend_rehearsal import ServerSession
    from tests.test_deployment_account_directory import Connection
    before=directory()
    config=AccountDirectorySettings(str(uuid4()),ISSUER,(('owner','workspace-owner'),))
    # Initialize the existing-ledger test connection before inviting, so both
    # invitation and enrollment use the real transaction load/CAS/audit path.
    initial=propose_legacy_directory((LegacyOwner(Identity(ISSUER,'owner'),'workspace-owner','Dennis','Dennis account','schwab'),),Identity(ISSUER,'owner'))
    conn=Connection(config,initial);tx=DirectoryTransaction(conn,config);admin=ServerSession(initial.users[0].identity)
    tx.execute(admin,0,'invite_google_user',name='Matthew',email='matthew.test@gmail.com')
    tx.request_registration(session())
    d=tx.snapshot(admin);pending=d.users[-1]
    assert len(conn.audit)==2 and not pending.enabled and d.accounts==initial.accounts and d.grants==()
    with pytest.raises(PermissionError):tx.session(session())
    with pytest.raises(ValueError):tx.execute(admin,1,'configure_user',user_id=pending.id,enabled=True)
    tx.execute(admin,2,'configure_user',user_id=pending.id,enabled=True,admin=False)
    assert tx.session(session()).account_ids==() and not tx.session(session()).administrator
    assert len(conn.audit)==3
    assert any('FOR UPDATE' in sql for sql,args in conn.calls)
