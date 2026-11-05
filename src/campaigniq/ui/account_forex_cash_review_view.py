"""One-shot Forex source preview; no source replacement or publication control."""
from campaigniq.import_draft_bridge import RestoredUpload


def render(st,service,user,account_id,draft,*,key):
    if (draft.year,draft.month)!=(2026,10):
        return
    with st.expander('Forex cash source review'):
        st.caption('Compare a balanced Forex cash ledger and supplemental executions with this month’s saved Trade History and Forex Transaction Report. This review does not save files or change the monthly publication checks.')
        ledger=st.file_uploader('Full Forex cash ledger CSV',type=['csv'],key=key+':forex-cash-ledger')
        supplement=st.file_uploader('Supplemental USD/JPY Trade History CSV',type=['csv'],key=key+':forex-cash-executions')
        st.caption('The same original export may supply both sections. Agreement between its sections is not independent broker verification.')
        if st.button('Review Forex cash sources',key=key+':forex-cash-review',disabled=ledger is None or supplement is None):
            def restored(value):return RestoredUpload(value.name,'text/csv',value.getvalue())
            try:
                result=service.review_forex_cash_sources(user,account_id,draft.id,draft.revision,restored(ledger),restored(supplement))
                calc=result['calculation']
                st.table([{'Measure':label,'USD':str(calc[field])} for label,field in (
                    ('Opening cash','opening_cash_usd'),('Realized trading result','candidate_trading_usd'),
                    ('Financing','candidate_financing_usd'),('Net cash change','candidate_net_cash_change_usd'),
                    ('Ending cash','ending_cash_usd'))])
                st.success('Cash balances and supplied execution details match. No documents were saved and no month was published.')
                st.warning('Source differences remain documented. This preview does not clear the existing Forex publication blocker.')
                with st.expander('Source differences and exact review'):
                    st.json(result)
            except (ValueError,PermissionError,FileNotFoundError,UnicodeError) as error:
                st.error(str(error))
