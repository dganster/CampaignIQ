"""Account-scoped draft recovery; original bytes and financial state are immutable."""
from hashlib import sha256
import json

AUDIT_PREFIX='draft_recovery_receipt_v1:'
CONFIRMATIONS=frozenset({'no_trading_owner_confirmation_v1','forex_cash_source_choice_receipt_v1',
    'forex_daily_source_receipt_v1','forex_financial_policy_approval_v1','crypto_statement_sources_approval_v1',
    'futures_empty_opening_confirmation_v1','futures_empty_closing_confirmation_v1',
    'futures_statement_association_v1','gain_loss_account_confirmation_v1'})
LABELS={'schwab_brokerage_statement':'Brokerage statement','thinkorswim_trade_history':'Trade History',
    'schwab_realized_gain_loss':'Realized Gain/Loss','schwab_forex_transaction_report':'Forex transaction export',
    'schwab_forex_monthly_statement':'Forex monthly statement','no_trading_owner_confirmation_v1':'No-trading confirmation',
    'futures_monthly_statement_pdf_v1':'Futures monthly statement','crypto_statement_sources_approval_v1':'Crypto source approval'}

def label(role):return LABELS.get(role,role.replace('_',' ').removesuffix(' v1').capitalize())
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True,default=str)
def fingerprint(v):return sha256(canonical(v).encode()).hexdigest()

def metadata(session,draft):
    with session._operation() as conn:
        rows=conn.execute('''SELECT d.id::text,d.role,d.filename,d.media_type,d.sha256,octet_length(d.content),
            d.revision,d.superseded,d.uploaded_at::text FROM import_draft_documents d JOIN import_drafts p ON p.id=d.draft_id
            WHERE p.id=%s AND p.workspace=%s ORDER BY d.uploaded_at,d.id''',(draft.id,session.drafts.workspace)).fetchall()
    return [dict(id=r[0],role=r[1],filename=r[2],media_type=r[3],sha256=r[4],byte_size=r[5],revision=r[6],
                 superseded=r[7],saved_at=r[8],purpose=label(r[1])) for r in rows]

def selection(rows,action,document_id):
    active=[r for r in rows if not r['superseded'] and not r['role'].startswith(AUDIT_PREFIX)]
    if action=='restart':
        if document_id is not None:raise ValueError('Restart applies to the whole unpublished draft')
        chosen=active
    elif action=='remove':
        chosen=[r for r in active if r['id']==document_id]
        if len(chosen)!=1:raise ValueError('Select one current source document')
        # A source change cannot retain approvals of the previous source set.
        chosen+= [r for r in active if r['role'] in CONFIRMATIONS and r['id']!=document_id]
    else:raise ValueError('Choose Remove from draft or Start this month over')
    if not chosen:raise ValueError('No active documents or confirmations to clear')
    return sorted(chosen,key=lambda r:r['id'])

def _plan(service,session,draft,user,action,document_id):
    from campaigniq.persistence.deployment_account_directory import DirectoryTransaction
    directory,actor=DirectoryTransaction(session._connection,service.gateway._directory.settings)._actor(user)
    directory.authorize(actor.identity,session.account.account_id,write=True)
    if session.artifacts.list_keys(prefix=f'{draft.year:04d}-{draft.month:02d}-'):
        raise ValueError('This month has saved financial records. Published or partially generated months cannot be reset or edited.')
    rows=metadata(session,draft);chosen=selection(rows,action,document_id)
    body=dict(format='campaigniq.draft_recovery_review',version=1,account_id=session.account.account_id,
        draft_id=str(draft.id),year=draft.year,month=draft.month,revision=draft.revision,
        actor_id=actor.user_id,directory_revision=directory.revision,action=action,document_id=document_id,
        affected_documents=chosen,originals_preserved=True,financial_state_written=False,publication_authorized=False)
    return dict(review=body,review_sha256=fingerprint(body))

def inventory(service,user,account_id,draft_id,revision):
    if type(revision) is not int:raise ValueError('Current displayed revision required')
    with service.gateway.transaction(user,account_id) as session:
        draft=service._lock(session,draft_id)
        if draft.revision!=revision:raise ValueError('Draft changed; reload the saved documents')
        return dict(revision=revision,documents=metadata(session,draft),
            protected=bool(session.artifacts.list_keys(prefix=f'{draft.year:04d}-{draft.month:02d}-')))

def review(service,user,account_id,draft_id,revision,*,action,document_id=None):
    if type(revision) is not int:raise ValueError('Current displayed revision required')
    with service.gateway.transaction(user,account_id) as session:
        draft=service._lock(session,draft_id)
        if draft.revision!=revision:raise ValueError('Draft changed; review the current documents')
        return _plan(service,session,draft,user,action,document_id)

def _write(session,draft,plan):
    from datetime import datetime,timezone
    ids=[r['id'] for r in plan['affected_documents']]
    with session._operation() as conn:
        changed=conn.execute('''UPDATE import_draft_documents SET superseded=true
            WHERE draft_id=%s AND id=ANY(%s::uuid[]) AND NOT superseded''',(draft.id,ids))
        if changed.rowcount!=len(ids):raise ValueError('Source selection changed; no recovery committed')
        changed=conn.execute('''UPDATE import_drafts SET revision=revision+1,updated_at=now()
            WHERE id=%s AND workspace=%s AND revision=%s''',(draft.id,session.drafts.workspace,draft.revision))
        if changed.rowcount!=1:raise ValueError('Draft changed; no recovery committed')
    next_revision=draft.revision+1
    audit=dict(format='campaigniq.draft_recovery_receipt',version=1,review=plan,
        changed_at=datetime.now(timezone.utc).isoformat(),new_revision=next_revision+1,
        review_sha256=fingerprint(plan),financial_state_written=False,publication_authorized=False)
    # Uploading the append-only audit record advances the revision once more.
    actual=session.drafts.upload(draft.id,AUDIT_PREFIX+str(next_revision),'draft-recovery-'+str(next_revision)+'.json',
        'text/plain',(canonical(audit)+'\n').encode())
    return actual,audit

def execute(service,user,account_id,draft_id,revision,*,action,document_id=None,reviewed_sha256,confirm=False):
    if type(revision) is not int or confirm is not True or not isinstance(reviewed_sha256,str) or len(reviewed_sha256)!=64:
        raise ValueError('Current recovery review and explicit confirmation required')
    with service.gateway.transaction(user,account_id,write=True) as session:
        draft=service._lock(session,draft_id,write=True)
        if draft.revision!=revision:raise ValueError('Draft changed; review recovery again')
        reviewed=_plan(service,session,draft,user,action,document_id)
        if reviewed['review_sha256']!=reviewed_sha256:raise ValueError('Recovery selection or permission changed; review again')
        before=metadata(session,draft);new_revision,audit=_write(session,draft,reviewed['review'])
        after=metadata(session,session.drafts.get(draft.id));by_id={r['id']:r for r in after}
        ids={r['id'] for r in reviewed['review']['affected_documents']}
        for row in before:
            expected=dict(row,superseded=True) if row['id'] in ids else row
            if by_id.get(row['id'])!=expected:raise ValueError('Original document or history changed; recovery rolled back')
        new_rows=[r for r in after if r['id'] not in {x['id'] for x in before}]
        if len(new_rows)!=1 or new_rows[0]['role']!=AUDIT_PREFIX+str(draft.revision+1):raise ValueError('Recovery audit verification failed')
        raw=session.drafts.document(draft.id,new_rows[0]['role'])[2]
        if raw!=(canonical(audit)+'\n').encode() or session.drafts.get(draft.id).revision!=new_revision:
            raise ValueError('Recovery receipt differs; recovery rolled back')
        session._check()
        return dict(revision=new_revision,action=action,removed=len(ids),originals_preserved=True,
            owner_confirmation_required=True,financial_state_written=False,publication_authorized=False)
