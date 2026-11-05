"""Daily original source review and owner receipt; no financial publication."""
from hashlib import sha256
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.pdf_text import PdfTextExtractionError


def show(st, result):
    rows = []
    for boundary, field in (('Opening', 'statement_opening_quantities'), ('Last daily confirmation', 'last_daily_closing_quantities')):
        quantities = result[field]
        rows.extend(dict(Boundary=boundary, Pair=pair, Quantity=value) for pair, value in quantities.items())
        if not quantities:
            rows.append(dict(Boundary=boundary, Pair='No open Forex positions', Quantity='0'))
    st.table(rows)
    st.caption('Last independent daily report: '+result['last_independent_daily_boundary']+
        '. Cash export through '+result['export_month_end_date']+': $'+result['export_month_end_cash_usd']+'.')
    if result['combined_inventory_evidence_reconciled']:
        st.success('Published opening lots, every supplied daily position check and complete saved executions reconcile. No financial approval or publication was performed.')
    else:
        st.warning('Daily checks completed, but authoritative opening or later export evidence is incomplete.')
    for blocker in result['blockers']:
        st.warning(blocker)
    with st.expander('Daily Forex evidence and retained source differences'):
        st.json(result)


def render(st, service, user, account_id, draft, *, key, writable=True, published=False):
    st.caption('Supply a ZIP of the original October daily Forex PDFs. Saving preserves the PDFs, their original file inventory and your account/coverage confirmation. It does not approve monetary differences or publish the month. No monthly Forex statement is required.')
    archive = st.file_uploader('October daily Forex reports ZIP', type=['zip'], key=key+':forex-daily-zip')
    state_key = key+':forex-daily-review-state'
    signature = (account_id, str(draft.id), draft.revision,
        sha256(archive.getvalue()).hexdigest() if archive is not None else None)
    prior = st.session_state.get(state_key)
    if prior is not None and prior[0] != signature:
        st.session_state.pop(state_key, None)
    errors = (ValueError, PermissionError, FileNotFoundError, UnicodeError, ArithmeticError, PdfTextExtractionError)
    if st.button('Validate Forex opening and closing positions', key=key+':forex-inventory-review', disabled=archive is None):
        st.session_state.pop(state_key, None)
        try:
            result = service.forex_daily_inventory_preview(user, account_id, draft.id, draft.revision,
                RestoredUpload(archive.name, 'application/zip', archive.getvalue()))
            st.session_state[state_key] = (signature, result)
        except errors as error:
            st.error(str(error))
    reviewed = st.session_state.get(state_key)
    if reviewed is not None:
        result = reviewed[1]
        show(st, result)
        if writable and not published and callable(getattr(service, 'save_forex_daily_sources', None)):
            choice = key+':daily-source-choice:'+str(draft.revision)+':'+result['review_sha256']
            same = st.checkbox('I confirm these daily Forex reports belong to the selected account', key=choice+':same')
            coverage = st.checkbox('I accept the displayed daily coverage limits and separate later cash-export evidence', key=choice+':coverage')
            replace = st.checkbox('Replace prior daily source confirmation; preserve earlier originals', key=choice+':replace')
            if st.button('Save daily Forex source confirmation', key=choice+':save', disabled=not (same and coverage)):
                try:
                    service.save_forex_daily_sources(user, account_id, draft.id, draft.revision,
                        RestoredUpload(archive.name, 'application/zip', archive.getvalue()),
                        reviewed_preview_sha256=result['review_sha256'], confirm_same_account=same,
                        confirm_coverage_limits=coverage, replace=replace)
                    st.session_state.pop(state_key, None)
                    st.rerun()
                except errors as error:
                    st.error(str(error))
    if callable(getattr(service, 'forex_daily_source_preview', None)):
        if st.button('View saved daily Forex source review', key=key+':forex-daily-saved'):
            try:
                result = service.forex_daily_source_preview(user, account_id, draft.id, draft.revision)
                show(st, result)
                st.success('Saved original daily PDFs and owner receipt verified. No month was published.')
            except errors as error:
                st.error(str(error))
