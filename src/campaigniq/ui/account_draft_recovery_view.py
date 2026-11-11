"""Visible saved sources and explicitly reviewed unpublished draft recovery."""
from campaigniq.account_draft_recovery import AUDIT_PREFIX, CONFIRMATIONS, label


def render(st,service,user,account_id,draft,*,key,writable,published):
    inventory=service.saved_documents(user,account_id,draft.id,draft.revision)
    rows=inventory['documents']
    active=[r for r in rows if not r['superseded'] and not r['role'].startswith(AUDIT_PREFIX)]
    st.subheader('Saved documents')
    sources=[r for r in active if r['role'] not in CONFIRMATIONS]
    if sources:st.table([dict(Document=r['purpose'],Filename=r['filename'],Status='Saved') for r in sources])
    else:st.info('No source documents saved for this month.')
    with st.expander('Upload and recovery history'):
        st.table([dict(Document=r['purpose'],Filename=r['filename'],Status='Superseded' if r['superseded'] else 'Current') for r in rows])
    roles={r['role'] for r in active}
    if not writable or published or inventory['protected']:return roles
    with st.expander('Correct this draft or start over'):
        st.caption('Original uploads stay in history. Recovery clears saved confirmations; review and confirm the updated month again. Published months cannot be changed here.')
        choices={r['id']:r for r in active}
        selected=st.selectbox('Saved document or confirmation to remove',list(choices),index=None,
            format_func=lambda v:label(choices[v]['role'])+' — '+choices[v]['filename'],key=key+':remove-document')
        action=None
        if st.button('Review removal',disabled=selected is None,key=key+':remove-review'):action='remove'
        if st.button('Review Start this month over',disabled=not active,key=key+':restart-review'):action='restart'
        plan_key=key+':recovery-plan'
        if action:
            st.session_state[plan_key]=service.draft_recovery_preview(user,account_id,draft.id,draft.revision,
                action=action,document_id=selected if action=='remove' else None)
        reviewed=st.session_state.get(plan_key)
        if reviewed:
            plan=reviewed['review']
            st.warning('These documents and confirmations will leave the active draft. Their originals remain in history.')
            st.table([dict(Document=r['purpose'],Filename=r['filename']) for r in plan['affected_documents']])
            confirm=st.checkbox('I confirm this correction to the unpublished month',key=key+':recovery-confirm:'+reviewed['review_sha256'])
            if st.button('Start this month over' if plan['action']=='restart' else 'Remove from this draft',disabled=not confirm,key=key+':recovery-apply'):
                service.recover_draft(user,account_id,draft.id,draft.revision,action=plan['action'],document_id=plan['document_id'],
                    reviewed_sha256=reviewed['review_sha256'],confirm=confirm)
                stable=key.rsplit(':',1)[0]
                st.session_state[stable+':notice']='Draft corrected. Original uploads were preserved. Review and confirm this month again.'
                if plan['action']=='restart':st.session_state.pop(stable+':activity',None)
                st.rerun()
            if st.button('Cancel correction',key=key+':recovery-cancel'):
                st.session_state.pop(plan_key,None);st.rerun()

    return roles
