"""One-shot daily Forex PDF inventory review, without a monthly-statement requirement."""
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.pdf_text import PdfTextExtractionError


def render(st, service, user, account_id, draft, *, key):
    st.caption('After saving the Forex cash source confirmation, supply a ZIP of the original October daily Forex PDFs. A monthly Forex statement is not required. The review preserves the distinction between independent daily confirmations and later unchanged export balances.')
    archive = st.file_uploader('October daily Forex reports ZIP', type=['zip'], key=key+':forex-daily-zip')
    if st.button('Validate Forex opening and closing positions', key=key+':forex-inventory-review', disabled=archive is None):
        try:
            result = service.forex_daily_inventory_preview(user, account_id, draft.id, draft.revision,
                RestoredUpload(archive.name, 'application/zip', archive.getvalue()))
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
        except (ValueError, PermissionError, FileNotFoundError, UnicodeError, ArithmeticError, PdfTextExtractionError) as error:
            st.error(str(error))
