"""One explicit read-only review, scoped to the current displayed inputs and choices."""
from datetime import date

def render(st,service,user,account_id,draft,*,key,signature,forex,crypto,futures,no_trading):
    month_label=date(draft.year,draft.month,1).strftime('%B %Y')
    state=key+':monthly-review'
    stored=st.session_state.get(state)
    if stored is not None and stored['signature']!=signature:
        st.session_state.pop(state,None);stored=None
    if st.button('Review month',key=key+':review-month'):
        st.session_state.pop(state,None)
        try:
            if no_trading:
                result=service.no_trading_preview(user,account_id,draft.id,draft.revision)
                ready=result['owner_confirmed'] is True
                details=[dict(Check='Statement, carried positions and owner confirmation',Ready=ready)]
            else:
                result=service.monthly_source_preview(user,account_id,draft.id,draft.revision,
                    forex_applicable=forex,crypto_applicable=crypto,futures_applicable=futures)
                ready=result['all_source_controls_ready'] is True
                details=[dict(Check=row['role'],Ready=row['valid'],Detail=row.get('message','')) for row in result['validations']]
                details.append(dict(Check='Closing positions and selected-market controls',Ready=ready))
                for finding in result['source_findings']:st.warning(finding)
            st.session_state[state]=dict(signature=signature,ready=ready,details=details)
        except (ValueError,PermissionError,FileNotFoundError) as error:
            st.session_state[state]=dict(signature=signature,ready=False,error=str(error))
    stored=st.session_state.get(state)
    if stored is None:
        st.info(f'Save the documents for {month_label}, then select Review month.')
        return False
    if 'error' in stored:
        st.error(month_label+': '+stored['error'])
        return False
    st.table(stored['details'])
    if stored['ready']:st.success('Review passed. Confirm the account and month below to publish. Publication rechecks the current sources and your access.')
    else:st.info(f'Complete or correct the failed checks above for {month_label}, then select Review month again.')
    return stored['ready'] is True
