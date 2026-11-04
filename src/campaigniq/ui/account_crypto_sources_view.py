"""Explicit two-PDF owner review; saved revisions and source hashes bind approval."""
from campaigniq.import_draft_bridge import RestoredUpload
from hashlib import sha256


def render(st,service,user,account_id,draft,*,key,writable,published):
    with st.expander('Crypto statements and source review'):
        st.caption('Crypto has its own account. Supply the previous and current monthly PDFs, plus the saved Trade History. '
            'Your confirmation links these exact documents to this account; it is not broker verification.')
        if writable and not published:
            previous=st.file_uploader('Previous month crypto statement PDF',type=['pdf'],key=key+':crypto-prior')
            current=st.file_uploader('Current month crypto statement PDF',type=['pdf'],key=key+':crypto-current')
            fingerprints=tuple(sha256(u.getvalue()).hexdigest() if u is not None else None for u in (previous,current))
            if st.session_state.get(key+':crypto-files')!=fingerprints:
                st.session_state.pop(key+':crypto-reviewed',None)
                st.session_state[key+':crypto-files']=fingerprints
            if st.button('Review crypto sources',key=key+':crypto-review'):
                if previous is None or current is None:st.error('Choose both monthly crypto statement PDFs first.')
                else:
                    reviewed=service.review_crypto_statements(user,account_id,draft.id,draft.revision,
                        RestoredUpload(previous.name,previous.type,previous.getvalue()),
                        RestoredUpload(current.name,current.type,current.getvalue()))
                    st.session_state[key+':crypto-reviewed']=reviewed
            reviewed=st.session_state.get(key+':crypto-reviewed')
            if reviewed is not None:
                show_calculation(st,reviewed['calculation'])
                with st.form(key+':crypto-approval'):
                    same=st.checkbox('I confirm both crypto statements and the Trade History belong to this selected account',key=key+':crypto-same')
                    choices=st.checkbox('I approve the displayed statement-based costs and cash choices, with source differences retained',key=key+':crypto-choices')
                    replace=st.checkbox('Replace prior crypto source approval; preserve earlier originals',key=key+':crypto-replace')
                    if st.form_submit_button('Save reviewed crypto sources'):
                        if previous is None or current is None:st.error('Choose both monthly crypto statement PDFs first.')
                        elif not same or not choices:st.error('Confirm the account association and displayed source choices before saving.')
                        else:
                            service.save_crypto_statement_sources(user,account_id,draft.id,draft.revision,
                                RestoredUpload(previous.name,previous.type,previous.getvalue()),
                                RestoredUpload(current.name,current.type,current.getvalue()),
                                reviewed_preview_sha256=reviewed['reviewed_preview_sha256'],
                                confirm_same_crypto_account=same,confirm_source_choices=choices,replace=replace)
                            st.rerun()
        if st.button('View saved crypto source review',key=key+':crypto-saved'):
            result=service.crypto_statement_preview(user,account_id,draft.id,draft.revision)
            show_calculation(st,result['calculation'])
            st.success('Exact saved PDFs, account source approval and calculation verified. No month was published.')


def show_calculation(st,calculation):
    t=calculation['totals']
    st.dataframe([dict(Measure=label,USD=t[field]) for label,field in (
        ('Documented opening acquisition cost','documented_opening_cost_usd'),
        ('Purchases including fees','purchases_including_fees_usd'),
        ('Fees already included above','fees_already_in_trade_cash_usd'),
        ('Ending cost basis','full_ending_cost_basis_usd'),
        ('Realized sales result','proposed_realized_sales_pnl_usd'),
        ('Ending cash','proposed_ending_cash_usd'))],hide_index=True)
    st.caption('Unknown costs remain unknown. Market value is not acquisition cost. Fees are counted once. '
        'Crypto results remain separate from securities and futures campaign totals.')
    st.warning('Original export and statement differences remain retained; approval does not erase or repair them.')
    with st.expander('Exact source differences and cost evidence'):
        st.json(calculation['review'])
