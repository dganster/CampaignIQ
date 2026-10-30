"""Read-only published records from the retained local workspace copy."""


def render(st,service,server_user,account_id,*,deployment=False):
    if type(deployment) is not bool:raise ValueError("Explicit application view mode required")
    try:
        with st.expander('Existing account published results'):
            st.caption('Saved account results; no import or reconciliation is rerun.' if deployment else
                'Data source: retained LOCAL workspace copy. Production updates may not be present here. Saved results only; no import or reconciliation is rerun.')
            months=service.published_months(server_user,account_id)
            if not months:
                st.info('No published month-end lot states are available for this account.' if deployment else 'No published month-end lot states are available in this local workspace copy.')
                return
            key='legacy-results:'+service._store.registry_id+':'+account_id
            selected=st.selectbox('Existing published month',months,key=key+':month')
            if st.button('View existing published results',key=key+':view'):
                year,month=map(int,selected.split('-'))
                result=service.published_results(server_user,account_id,year,month)
                st.subheader('Existing account results · '+result.month)
                st.caption(str(result.source_artifacts)+' retained artifacts match the existing storage adapter.')
                st.dataframe([dict(Instrument=name,Quantity=quantity) for name,quantity in result.positions],hide_index=True)
                with st.expander('Existing closing lots and source basis'):
                    st.dataframe([dict(Instrument=row[0],Quantity=row[1],Basis=row[2],Source=row[3],Campaign=row[4])
                        for row in result.lots],hide_index=True)
                st.write('Saved broker realized gain/loss, USD: '+result.brokerage_realized_usd)
                if result.realized:
                    st.dataframe([dict(Date=row[0],Instrument=row[1],Quantity=row[2],Proceeds=row[3],Basis=row[4],Gain_loss=row[5])
                        for row in result.realized],hide_index=True)
                else:st.caption('No broker realized records in this saved month.')
                if result.forex_settled_usd is not None:
                    st.write('Saved Forex settled P/L, USD: '+result.forex_settled_usd)
                    st.caption('Settlement P/L only; excludes financing and other cash activity.')
                st.caption('Crypto evidence: '+result.crypto_status)
                if result.crypto_positions:
                    st.dataframe([dict(Crypto=name,Quantity=quantity) for name,quantity in result.crypto_positions],hide_index=True)
                for warning in result.warnings:st.warning(warning)
    except PermissionError:
        st.error('Current sign-in cannot read this existing account. Reload the account list.')
    except Exception:
        st.error('Existing published results could not be verified. No partial report is displayed.')
