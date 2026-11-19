"""Present retained crypto source limitations without changing evidence."""


def has_statement_choices(report):
    body = report.get('approved_statement_financials')
    return (isinstance(body, dict)
            and body.get('format') == 'campaigniq.approved_crypto_financial_candidate'
            and isinstance(body.get('calculation'), dict)
            and body.get('approval') == {
                'source_receipt_verified': True, 'account_association': 'owner_confirmed',
                'broker_verified_account_link': False})


def render_crypto_limitations(ui, *, status='', warnings=(), report=None):
    """Summaries lack full controls: never infer reconciliation from approval."""
    ui.markdown('#### Crypto source limitations')
    approved = report is not None and has_statement_choices(report)
    ui.caption('Crypto is separate from brokerage and Forex totals. Owner approval does not establish independent reconciliation.')
    with ui.expander('How crypto sources are used'):
        if approved:
            ui.caption('Published holdings, acquisition costs, FIFO sales results, and closing cash use owner-approved statement financial choices. Individual fills, recorded fees, execution prices, the cash ledger, and export snapshot quantities below retain original export observations.')
            ui.caption('Opening units use the preceding statement; opening acquisition costs require separate documented acquisition rows. Unknown cost remains unknown.')
        elif report is not None:
            ui.caption('The displayed figures use retained crypto evidence. No owner-approved statement financial record is identified in this view. A retained statement alone does not establish reconciliation.')
        else:
            ui.caption('This published summary does not include per-field source controls. Open Crypto for the detailed statement/export distinction.')
    if status:
        ui.caption('Saved evidence status: ' + status)
    items = []
    if report is not None:
        if report.get('cash_control_reconciled') is not True:
            items.append('Original export: cash running balances are incomplete or mismatched; the export does not establish a verified closing balance.')
        if report.get('fill_control_reconciled') is not True:
            items.append('Original export: execution fills and cash-ledger fills need review.')
        if report.get('snapshot_mismatches'):
            items.append('Original export: computed quantities differ from the snapshot or snapshot evidence is missing. This retained comparison does not establish the status of statement-based holdings.')
    common = {
        'Crypto cash running-balance control is incomplete or mismatched.': 'Original export: cash running balances are incomplete or mismatched; the export does not establish a verified closing balance.',
        'Crypto execution history and cash-ledger fills do not match; amounts and fees need review.': 'Original export: execution fills and cash-ledger fills need review.',
        'Crypto holdings are an undated export snapshot, not an independently verified month-end statement.': 'Original export: snapshot holdings have no independently verified month-end date.',
        'Opening crypto quantities are inferred from this export; no previous published crypto state is available.': 'Original export calculation: opening units were inferred because no previous published crypto state was available.',
        'Statement-based financial choices approved by owner; original source discrepancies remain unresolved.': 'Owner-approved statement financial choices do not resolve the original source discrepancies.',
    }
    items.extend(common.get(warning, warning) for warning in warnings)
    items = list(dict.fromkeys(items))
    if items:
        ui.warning('\n\n'.join('- ' + item for item in items))
    elif report is None:
        ui.caption('No source warnings were included in this summary; this is not an independent verification result.')
    with ui.expander('Crypto control details'):
        ui.caption('Owner approval is separate from independent source reconciliation. Crypto remains separate from brokerage and Forex totals.')
        if report is not None:
            passed = []
            if report.get('cash_control_reconciled') is True:
                passed.append('cash running balances within the export')
            if report.get('fill_control_reconciled') is True:
                passed.append('execution fills against export cash-ledger fills')
            if passed:
                ui.caption('Matching export controls: ' + '; '.join(passed) + '. These checks do not by themselves verify month-end holdings or resolve statement/export differences.')
            if report.get('crypto_statement'):
                ui.caption('A dated crypto statement is retained with this report.')
            if report.get('statement_fills_reconciled') is True and report.get('statement_ending_balances_reconciled') is True:
                ui.caption('Saved statement controls report matching fills, ending quantities, and ending cash.')
