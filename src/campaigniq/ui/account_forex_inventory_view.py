"""One-shot Forex statement inventory review; no source save or approval controls."""
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.pdf_text import PdfTextExtractionError


def render(st, service, user, account_id, draft, *, key):
    st.caption('After saving the Forex cash source confirmation, supply the October Forex monthly statement. The report headed Customer Account Summary Report is separate from the futures/options statement. This review checks positions without saving the PDF or publishing the month.')
    monthly = st.file_uploader('October Forex monthly statement PDF', type=['pdf'], key=key+':forex-inventory-pdf')
    if st.button('Validate Forex opening and closing positions', key=key+':forex-inventory-review', disabled=monthly is None):
        try:
            result = service.forex_inventory_preview(user, account_id, draft.id, draft.revision,
                RestoredUpload(monthly.name, 'application/pdf', monthly.getvalue()))
            rows = []
            for boundary, field in (('Opening', 'statement_opening_quantities'), ('Closing', 'statement_closing_quantities')):
                quantities = result[field]
                rows.extend(dict(Boundary=boundary, Pair=pair, Quantity=value) for pair, value in quantities.items())
                if not quantities:
                    rows.append(dict(Boundary=boundary, Pair='No open Forex positions', Quantity='0'))
            st.table(rows)
            if result['authoritative_opening_matches'] and result['authoritative_replay_closing_matches']:
                st.success('Published opening quantities and replayed closing positions match the Forex statement. No financial approval or publication was performed.')
            else:
                st.warning('Statement positions reconcile, but published opening-state verification is incomplete.')
            for blocker in result['blockers']:
                st.warning(blocker)
            with st.expander('Forex inventory evidence and retained source differences'):
                st.json(result)
        except (ValueError, PermissionError, FileNotFoundError, UnicodeError, PdfTextExtractionError) as error:
            st.error(str(error))
