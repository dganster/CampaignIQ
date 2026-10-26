"""Local read-only comparison controls; quantities remain attributed to each account."""
from campaigniq.account_selection_preview import SelectedDraft,compare_selected


def render_selection(st,service,actor,accounts,year,month,key,current_account):
    with st.expander('Compare accounts for this month'):
        st.caption(f'{year:04d}-{month:02d} • Separate account results, with optional quantity totals. '
                   'No combined campaign histories, profit/loss approval or publication.')
        ids=st.multiselect('Accounts to compare',list(accounts),default=[current_account],
            format_func=lambda value:accounts[value].name+' · '+value[:8],key='selection:'+key)
        requests=[];missing=[]
        for account_id in ids:
            st.markdown('**'+accounts[account_id].name+'**')
            drafts=[d for d in service.drafts(actor.identity,account_id) if (d.year,d.month)==(year,month)]
            if len(drafts)!=1:
                missing.append(accounts[account_id].name)
                continue
            draft=drafts[0]
            itemkey=key+':'+account_id+':'+str(draft.id)+':'+str(draft.revision)
            forex=st.checkbox('Forex applicable — '+accounts[account_id].name,value=True,key='group-forex:'+itemkey)
            crypto=st.checkbox('Crypto applicable — '+accounts[account_id].name,value=True,key='group-crypto:'+itemkey)
            requests.append(SelectedDraft(account_id,draft.id,draft.revision,forex,crypto))
        totals=st.checkbox('Show quantity totals',value=False,key='group-totals:'+key)
        if st.button('Compare saved account months',key='group-run:'+key):
            if not ids:
                st.info('Select at least one account.')
                return
            if missing:
                st.warning('A saved draft for this month is missing in: '+', '.join(missing))
                return
            result=compare_selected(service,actor.identity,year,month,tuple(requests),include_totals=totals)
            if result.all_inventory_reconciled:
                st.success('All selected accounts reconcile with their supplied statements.')
            else:
                st.warning('A selected account has unresolved closing inventory or source controls.')
            for account in result.accounts:
                st.markdown('**'+accounts[account.account_id].name+' · '+account.account_id[:8]+'**')
                st.write('Saved revision '+str(account.revision)+' · '+('Reconciled' if account.inventory_reconciled else 'Unresolved'))
                if account.closing_quantities:
                    st.dataframe([dict(Instrument=name,Quantity=quantity) for name,quantity in account.closing_quantities],hide_index=True)
                if account.mismatches:
                    st.dataframe([dict(Instrument=row.instrument,Computed=row.computed,Statement=row.statement,Difference=row.difference)
                                  for row in account.mismatches],hide_index=True)
                for finding in account.source_findings:st.warning(finding)
            if totals:
                if not result.all_inventory_reconciled:
                    st.info('Quantity totals are withheld until every selected account reconciles.')
                elif result.quantity_totals:
                    st.markdown('**Quantity totals across the selected accounts**')
                    st.dataframe([dict(Instrument=name,Quantity=quantity) for name,quantity in result.quantity_totals],hide_index=True)
