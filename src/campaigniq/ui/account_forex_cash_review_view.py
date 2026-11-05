"""Forex source review and owner confirmation; never publishes financial state."""
from hashlib import sha256
from campaigniq.import_draft_bridge import RestoredUpload


def show(st,result):
    calc=result['calculation']
    st.table([{'Measure':label,'USD':str(calc[field])} for label,field in (
        ('Opening cash','opening_cash_usd'),('Realized trading result','candidate_trading_usd'),
        ('Financing','candidate_financing_usd'),('Net cash change','candidate_net_cash_change_usd'),
        ('Ending cash','ending_cash_usd'))])
    st.warning('Original source differences are retained. This source confirmation does not clear the monthly publication blocker.')
    with st.expander('Source differences and exact review'):st.json(result)


def render(st,service,user,account_id,draft,*,key,writable=True,published=False):
    if (draft.year,draft.month)!=(2026,10):return
    with st.expander('Forex cash source review'):
        st.caption('Review the Forex cash ledger and supplemental executions with this month’s saved sources. Saving preserves these exact originals and your source choice; it does not publish the month.')
        ledger=st.file_uploader('Full Forex cash ledger CSV',type=['csv'],key=key+':forex-cash-ledger')
        supplement=st.file_uploader('Supplemental USD/JPY Trade History CSV',type=['csv'],key=key+':forex-cash-executions')
        st.caption('The same original export may supply both sections. Agreement between sections is not independent broker verification.')
        state_key=key+':forex-review-state'
        signature=(account_id,str(draft.id),draft.revision,
            sha256(ledger.getvalue()).hexdigest() if ledger is not None else None,
            sha256(supplement.getvalue()).hexdigest() if supplement is not None else None)
        prior=st.session_state.get(state_key)
        if prior is not None and prior[0]!=signature:st.session_state.pop(state_key,None)
        def restored(value):return RestoredUpload(value.name,'text/csv',value.getvalue())
        errors=(ValueError,PermissionError,FileNotFoundError,UnicodeError)
        if st.button('Review Forex cash sources',key=key+':forex-cash-review',disabled=ledger is None or supplement is None):
            st.session_state.pop(state_key,None)
            try:
                result=service.review_forex_cash_sources(user,account_id,draft.id,draft.revision,restored(ledger),restored(supplement))
                st.session_state[state_key]=(signature,result)
            except errors as error:st.error(str(error))
        reviewed=st.session_state.get(state_key)
        if reviewed is not None:
            result=reviewed[1];show(st,result)
            # Include the exact review digest in confirmation keys to avoid
            # carrying checks across changed source files or revised documents.
            choice_key=key+':forex-choice:'+str(draft.revision)+':'+result['review_sha256']
            same=st.checkbox('I confirm these Forex sources belong to the selected account',key=choice_key+':same')
            choices=st.checkbox('Use the displayed balanced cash figures, with original source differences retained',key=choice_key+':choices')
            replace=st.checkbox('Replace prior Forex source confirmation; preserve earlier originals',key=choice_key+':replace')
            if st.button('Save Forex source confirmation',key=choice_key+':save',disabled=not(same and choices)):
                try:
                    service.save_forex_cash_sources(user,account_id,draft.id,draft.revision,restored(ledger),restored(supplement),
                        reviewed_preview_sha256=result['review_sha256'],confirm_same_account=same,confirm_source_choices=choices,replace=replace)
                    st.session_state.pop(state_key,None);st.rerun()
                except errors as error:st.error(str(error))
        if st.button('View saved Forex source review',key=key+':forex-cash-saved'):
            try:
                result=service.forex_cash_source_preview(user,account_id,draft.id,draft.revision)
                show(st,result)
                st.success('Exact saved Forex originals and owner confirmation verified. No month was published.')
            except errors as error:st.error(str(error))

        if callable(getattr(service,'forex_inventory_preview',None)):
            from campaigniq.ui.account_forex_inventory_view import render as render_inventory
            render_inventory(st,service,user,account_id,draft,key=key,writable=writable,published=published)
