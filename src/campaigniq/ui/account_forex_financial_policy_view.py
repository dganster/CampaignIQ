"""Review a monetary proposal and separately approve its exact source policy."""
from campaigniq.pdf_text import PdfTextExtractionError

ERRORS = (ValueError, PermissionError, FileNotFoundError, UnicodeError, ArithmeticError, PdfTextExtractionError)


def show(st, result, *, approved=False):
    calculation = result['artifact']['calculation']
    labels={'opening_cash_usd':'Opening cash: daily sessions','trading_usd':'Trading P/L: daily close controls',
        'financing_usd':'Financing: daily sessions','ending_cash_usd':'Ending cash','net_cash_change_usd':'Net cash change'}
    st.table([dict(Measure=labels.get(k,k), USD=v) for k,v in calculation['totals'].items()])
    st.table([{'Execution reference':row['reference'],'Pair':row.get('pair',''),
        'Daily close P/L, USD':row['candidate_pl_usd'],'Cash export P/L, USD':row.get('cash_export_pl_usd','Not supplied'),
        'Original CSV P/L, USD':row.get('csv_pl_usd') if row.get('csv_pl_usd') is not None else 'Not reported'}
        for row in calculation['closes']])
    comparison=calculation.get('calendar_comparison')
    if comparison:
        names={'original_opening_cash_usd':'Original calendar export: opening cash',
            'original_trade_cash_usd':'Original calendar export: trading cash',
            'original_financing_usd':'Original calendar export: financing',
            'prior_calendar_financing_in_daily_sessions_usd':'Prior-calendar financing included in daily sessions',
            'daily_derived_calendar_opening_usd':'Daily-derived calendar opening cash',
            'derived_opening_minus_export_usd':'Daily-derived opening minus original export',
            'daily_closes_minus_export_usd':'Daily close dollars minus original export'}
        st.caption('Calendar cash comparison and retained differences')
        st.table([dict(Measure=names.get(k,k),USD=v) for k,v in comparison.items()])
    original=calculation.get('raw_csv_controls')
    if original:
        st.table([dict(Measure='Original Forex CSV MTD P/L',USD=original['mtd_settled_pl_usd']),
            dict(Measure='Original CSV MTD P/L minus candidate',USD=calculation['original_csv_mtd_minus_candidate_usd'])])
    differences=result['artifact'].get('original_cash_source_differences',{}).get('saved_history_cash_differences',[])
    if differences:
        st.warning('The saved original trade history has cash-row differences; they remain original evidence.')
        st.table([{'Reference':r['reference'],'Reported cash, USD':str(r['reported']),
            'Running-balance change, USD':str(r['balance_change'])} for r in differences])
    st.caption('Last independent daily confirmation: '+calculation['last_independent_daily_boundary']+
        '. Later unchanged cash-export evidence remains separate.')
    st.success('Every daily/calendar monetary equation reconciles exactly. Original source differences remain retained.')
    if not calculation['all_original_financing_rows_classified']:
        st.warning('Some original CSV financing rows have no exact daily-Forex or securities-cash match; their treatment remains unresolved.')
    for limitation in calculation['limitations']:
        if not approved or 'durable financial-policy approval' not in limitation:st.warning(limitation)
    with st.expander('Financial candidate, original controls and exact source fingerprints'):
        st.json(result)


def render(st, service, user, account_id, draft, *, key, writable=True, published=False):
    state_key=key+':forex-financial-review-state'
    signature=(user,account_id,str(draft.id),draft.revision)
    prior=st.session_state.get(state_key)
    if prior is not None and prior[0]!=signature:st.session_state.pop(state_key,None)
    with st.expander('Forex financial candidate and monetary approval'):
        st.caption('Calculate close-day profit/loss and financing from the saved daily PDFs and original cash sources. '
            'Review and monetary-policy approval are separate actions. Approval does not publish October.')
        if st.button('Calculate saved Forex financial candidate',key=key+':forex-financial-candidate'):
            st.session_state.pop(state_key,None)
            try:
                result=service.forex_financial_candidate_preview(user,account_id,draft.id,draft.revision)
                st.session_state[state_key]=(signature,result)
            except ERRORS as error:st.error(str(error))
        reviewed=st.session_state.get(state_key)
        if reviewed is not None:
            result=reviewed[1];show(st,result)
            if writable and not published and callable(getattr(service,'save_forex_financial_policy',None)):
                st.caption('Only the account owner can save this monetary approval. Original documents and financial records are preserved.')
                choice=key+':forex-money-choices:'+str(draft.revision)+':'+result['artifact_sha256']
                basis=st.checkbox('I approve the displayed daily reporting-session monetary basis',key=choice+':basis')
                differences=st.checkbox('I accept the displayed candidate amounts while retaining the original CSV differences',key=choice+':differences')
                coverage=st.checkbox('I accept the stated daily coverage limit and separate later cash-export evidence',key=choice+':coverage')
                classified=result['artifact']['calculation']['all_original_financing_rows_classified']
                if st.button('Approve Forex monetary basis',key=choice+':save',disabled=not (basis and differences and coverage and classified)):
                    try:
                        service.save_forex_financial_policy(user,account_id,draft.id,draft.revision,
                            reviewed_candidate_sha256=result['artifact_sha256'],confirm_daily_basis=basis,
                            confirm_retained_differences=differences,confirm_coverage_limits=coverage)
                        st.session_state.pop(state_key,None);st.rerun()
                    except ERRORS as error:
                        st.session_state.pop(state_key,None);st.error(str(error))
        if callable(getattr(service,'forex_financial_policy_preview',None)):
            if st.button('View saved Forex monetary approval',key=key+':forex-money-saved'):
                try:
                    saved=service.forex_financial_policy_preview(user,account_id,draft.id,draft.revision)
                    show(st,saved['candidate'],approved=True)
                    st.success('Owner monetary-policy approval and exact original sources verified. No month was published.')
                    st.warning(saved['publication_blocker'])
                    with st.expander('Saved monetary approval receipt'):st.json(saved['receipt'])
                except ERRORS as error:st.error(str(error))
