"""Explicit account selection and optional position totals; read-only LOCAL UI."""
from hashlib import sha256

from campaigniq.account_signedin_comparison import PublishedSelection


def render(st,service,server_user,*,deployment=False):
    if type(deployment) is not bool:raise ValueError("Explicit application view mode required")
    try:
        overview=service.gateway.overview(server_user)
        with st.expander('Compare published accounts'):
            st.caption(('Published months for new accounts only. ' if deployment else 'Local published months only. ')+
                'Account results remain separate. Combined quantities are optional; monetary results are never combined here.')
            if not overview.accounts:
                st.info('No new brokerage accounts are available for comparison.')
                return
            if not service.gateway.storage_ready(server_user):
                st.info('Account storage could not be verified.' if deployment else 'Enable local monthly input storage before comparing published accounts.')
                return
            accounts={a.id:a for a in overview.accounts}
            root='signedin-comparison:'+service.gateway._directory.registry_id+':'+overview.user_id
            selected=st.multiselect('Accounts to compare',list(accounts),default=[],
                format_func=lambda key:accounts[key].name+(' [archived]' if accounts[key].archived else '')+' · '+key[:8],
                key=root+':accounts')
            if not selected:
                st.info('Select one or more accounts to view a published month.')
                return
            by_account={}
            for account_id in selected:
                published={}
                for draft in service.drafts(server_user,account_id):
                    if service.published(server_user,account_id,draft.id,draft.revision):
                        published[f'{draft.year:04d}-{draft.month:02d}']=draft
                by_account[account_id]=published
            common=sorted(set.intersection(*(set(rows) for rows in by_account.values())))
            if not common:
                st.info('The selected accounts have no common published month. No accounts were omitted.')
                return
            month=st.selectbox('Published reporting month',common,key=root+':month:'+':'.join(selected))
            digest=sha256(('|'.join(selected)+'|'+month).encode()).hexdigest()
            key=root+':'+digest
            if key+':sources' not in st.session_state:
                st.session_state[key+':sources']=tuple(PublishedSelection(account_id,by_account[account_id][month].id,
                    by_account[account_id][month].revision) for account_id in selected)
            sources=st.session_state[key+':sources']
            st.caption('Displayed source revisions: '+', '.join(accounts[s.account_id].name+' '+str(s.revision) for s in sources))
            if st.button('Reload comparison sources',key=key+':reload'):
                del st.session_state[key+':sources']
                st.rerun()
            with st.form(key+':form'):
                combined=st.checkbox('Include combined position quantities',value=False,key=key+':combined')
                clicked=st.form_submit_button('View account comparison')
            if clicked:
                year,number=map(int,month.split('-'))
                result=service.compare(server_user,year,number,sources,include_positions=combined)
                st.subheader('Published account comparison · '+result.month)
                for row in result.accounts:
                    st.write(accounts[row.account_id].name)
                    st.caption('Broker realized gain/loss, USD: '+row.brokerage_realized_usd+
                        ' • '+str(row.original_documents)+' verified original documents')
                    st.dataframe([dict(Instrument=name,Quantity=quantity) for name,quantity in row.positions],hide_index=True)
                    if row.forex_applicable:
                        st.caption('Forex settled P/L, USD: '+row.forex_settled_usd+' (excludes financing and other cash activity)')
                    if getattr(row,'futures_applicable',False):
                        st.caption('Futures/options net P/L, USD: '+row.futures_realized_usd)
                        st.caption(row.futures_evidence_status)
                    if row.crypto_applicable:
                        st.caption('Crypto evidence status: '+row.crypto_status)
                        st.dataframe([dict(Crypto=name,Quantity=quantity) for name,quantity in row.crypto_positions],hide_index=True)
                        for warning in row.crypto_warnings:st.warning(warning)
                if result.includes_combined_positions:
                    st.write('Combined position quantities')
                    st.caption('Published lot-state quantities only. Crypto remains separate by account; quantities are not market values.')
                    st.dataframe([dict(Instrument=name,Quantity=quantity) for name,quantity in result.combined_positions],hide_index=True)
    except PermissionError:
        st.error('Current sign-in cannot access the entire selection. Reload the account list.')
    except (ValueError,FileNotFoundError):
        st.error('The entire comparison could not be verified. Reload comparison sources and check the selected published months.')
    except Exception:
        st.error('Account comparison stopped. No partial results are displayed.' if deployment else 'Local comparison stopped. No partial results are displayed.')
