"""One statement, one review and owner confirmation; publication remains separate."""
from decimal import Decimal

def render(st,service,user,account_id,draft,*,key,writable,published,guided=False,context=None):
    if guided:return render_guided(st,service,user,account_id,draft,key=key,writable=writable,published=published,context=context)
    selected=service.no_trading_status(user,account_id,draft.id,draft.revision)
    if selected:st.info('No-trading confirmation saved. Use the account/month confirmation below to publish.')
    with st.expander('No trading activity this month',expanded=selected or guided):
        st.caption('Upload the monthly Brokerage Statement above. Existing published lots and campaigns carry forward unchanged. Cash dividends and interest are shown separately; empty trading reports are not required.')
        state_key=key+':no-trading-review';signature=(account_id,str(draft.id),draft.revision)
        cached=st.session_state.get(state_key)
        if cached is not None and cached[0]!=signature:st.session_state.pop(state_key,None)
        if st.button('Review statement without trading',key=key+':no-trading-preview'):
            st.session_state.pop(state_key,None)
            try:st.session_state[state_key]=(signature,service.no_trading_preview(user,account_id,draft.id,draft.revision))
            except (ValueError,PermissionError,FileNotFoundError) as error:st.error(str(error))
        cached=st.session_state.get(state_key)
        if cached is not None:
            reviewed=cached[1];cash=reviewed['review']['financials']
            st.table([dict(Measure=label,USD=f'${Decimal(cash[field]):,.2f}') for label,field in
                (('Realized trading P/L','realized_trading_pnl_usd'),('Cash dividends / interest','income_usd'),('Opening cash','opening_cash_usd'),('Ending cash','ending_cash_usd'))])
            st.table(cash['cash_income'])
            st.success('Statement positions match the preceding published lots. Lot costs and campaign identities will be preserved.')
            if writable and not published and not reviewed['owner_confirmed']:
                choice=key+':no-trading-choice:'+reviewed['review_sha256']
                confirm=st.checkbox('I confirm no trading or position-changing activity this month',key=choice+':trading')
                income=st.checkbox('I reviewed the separate statement cash income',key=choice+':cash')
                if st.button('Save no-trading confirmation',key=choice+':save',disabled=not (confirm and income)):
                    try:
                        service.save_no_trading_confirmation(user,account_id,draft.id,draft.revision,
                            reviewed_sha256=reviewed['review_sha256'],confirm_no_trading=confirm,confirm_cash_income=income)
                        st.session_state.pop(state_key,None);st.rerun()
                    except (ValueError,PermissionError,FileNotFoundError) as error:st.error(str(error))
            elif reviewed['owner_confirmed']:st.info('No-trading confirmation saved. Use the separate account/month confirmation below to publish.')
    return selected


def render_guided(st,service,user,account_id,draft,*,key,writable,published,context):
    """One statement review, explicit owner choices, then separate publication."""
    selected=service.no_trading_status(user,account_id,draft.id,draft.revision)
    state_key=key+':guided-no-trading-review'
    attempt_key=key+':no-trading-publish-attempt'
    signature=(account_id,str(draft.id),draft.revision,context)
    cached=st.session_state.get(state_key)
    if cached is not None and cached[0]!=signature:
        st.session_state.pop(state_key,None);cached=None
    st.caption('Review the Brokerage Statement, unchanged positions and cash income. Confirm the review before authorizing publication below.')
    clicked=st.button('Review month',key=key+':no-trading-preview')
    # A saved owner receipt is rebuilt against current access, sources and lots.
    # After a failed publication attempt, require another explicit review.
    if clicked or (selected and not st.session_state.get(attempt_key,False)):
        st.session_state.pop(state_key,None)
        try:
            reviewed=service.no_trading_preview(user,account_id,draft.id,draft.revision)
            st.session_state[state_key]=(signature,reviewed)
            st.session_state[attempt_key]=False
        except (ValueError,PermissionError,FileNotFoundError) as error:
            st.error(str(error))
    cached=st.session_state.get(state_key)
    if cached is None:
        st.info('Save the Brokerage Statement, then review this month.');return False
    reviewed=cached[1];cash=reviewed['review']['financials']
    st.table([dict(Measure=label,USD=f'${Decimal(cash[field]):,.2f}') for label,field in
        (('Realized trading P/L','realized_trading_pnl_usd'),('Cash dividends / interest','income_usd'),('Opening cash','opening_cash_usd'),('Ending cash','ending_cash_usd'))])
    if cash['cash_income']:st.table(cash['cash_income'])
    st.success('Statement positions match the preceding published lots. Lot costs and campaign identities will be preserved.')
    if reviewed['owner_confirmed'] is True:
        if st.session_state.get(attempt_key,False):
            st.info('Review this month again before retrying publication.');return False
        st.success('No-trading review confirmed. Authorize publication for this account and month below.');return True
    if writable and not published:
        choice=key+':no-trading-choice:'+reviewed['review_sha256']
        confirm=st.checkbox('I confirm no trading or position-changing activity this month',key=choice+':trading')
        income=st.checkbox('I reviewed the separate statement cash income',key=choice+':cash')
        if st.button('Confirm reviewed month',key=choice+':save',disabled=not (confirm and income)):
            try:
                service.save_no_trading_confirmation(user,account_id,draft.id,draft.revision,
                    reviewed_sha256=reviewed['review_sha256'],confirm_no_trading=confirm,confirm_cash_income=income)
                st.session_state.pop(state_key,None);st.rerun()
            except (ValueError,PermissionError,FileNotFoundError) as error:
                st.session_state.pop(state_key,None);st.error(str(error))
    return False
