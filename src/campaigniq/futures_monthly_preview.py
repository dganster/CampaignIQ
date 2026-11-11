"""Monthly read-only integration with explicit derived-source lineage.

The original export is immutable. A securities/Forex/crypto projection is created
only after the full futures sources have passed the separate account preview.
No publisher may use this preview as authorization or omit futures or crypto.
"""
from calendar import monthrange
import csv
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq.account_futures_preview import connected_preview
from campaigniq.account_reconciliation_preview import ReadOnlyArtifacts
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_draft_bridge import restore_uploads
from campaigniq.import_preflight import prepare_monthly_import
from campaigniq.monthly_upload_staging import stage_monthly_uploads
from campaigniq.domain.value_objects.forex_pair import ForexPair

HISTORY='thinkorswim_trade_history'


def _table(content):
    text=content.decode('utf-8-sig')
    lines=text.splitlines(keepends=True)
    locations=[i for i,line in enumerate(lines) if line.strip()=='Account Trade History']
    if len(locations)!=1:raise ValueError('Missing or ambiguous Trade History table')
    start=locations[0]+1
    if start>=len(lines):raise ValueError('Missing Trade History columns')
    header=next(csv.reader([lines[start]]))
    columns={name:header.index(name) for name in ('Symbol','Exp','Exec Time')}
    rows=[]
    for index in range(start+1,len(lines)):
        if not lines[index].strip():break
        row=next(csv.reader([lines[index]]))
        if len(row)!=len(header):raise ValueError('Unsupported multiline or malformed Trade History row')
        rows.append((index,row))
    return lines,columns,rows


def has_futures(session,draft):
    for role in ('futures_empty_opening_confirmation_v1','futures_monthly_statement_pdf_v1',
                 'futures_statement_association_v1','futures_empty_closing_confirmation_v1'):
        try:session.drafts.document(draft.id,role)
        except FileNotFoundError:continue
        return True
    try:_,_,content=session.drafts.document(draft.id,HISTORY)
    except FileNotFoundError:return False
    lines,columns,rows=_table(content)
    if any(row[columns['Symbol']].strip().startswith('/') or row[columns['Exp']].strip().startswith('/') for _,row in rows):
        return True
    # Removing option execution rows cannot bypass the publication guard while
    # exercise, settlement or sweep evidence remains in the futures cash ledger.
    starts=[i for i,line in enumerate(lines) if line.strip()=='Futures Statements']
    if len(starts)>1:raise ValueError('Ambiguous futures ledger')
    if starts:
        start=starts[0]+1
        if start>=len(lines):raise ValueError('Missing futures ledger header')
        header=next(csv.reader([lines[start]]))
        if 'Type' not in header:raise ValueError('Missing futures ledger type column')
        for line in lines[start+1:]:
            if not line.strip():break
            row=next(csv.reader([line]))
            if len(row)!=len(header):raise ValueError('Malformed futures ledger row')
            if row[header.index('Type')].strip()!='BAL':return True
    return False


def project_history(content,result,destination):
    if sha256(content).hexdigest()!=result['source_sha256']:
        raise ValueError('Futures preview and original history fingerprints differ')
    lines,columns,rows=_table(content)
    deleted=[];source_ids=[]
    for ordinal,(index,row) in enumerate(rows):
        if row[columns['Symbol']].strip().startswith('/') or row[columns['Exp']].strip().startswith('/'):
            deleted.append(index);source_ids.append('history-row-'+str(ordinal))
    if not deleted or len(deleted)!=result['option_execution_legs']:
        raise ValueError('Every removed futures row must belong to the verified selected-month calculation')
    removed=set(deleted)
    derived=''.join(line for index,line in enumerate(lines) if index not in removed).encode('utf-8')
    destination.write_bytes(derived)
    return dict(format='campaigniq.derived_monthly_history_lineage',version=1,
        original_sha256=sha256(content).hexdigest(),derived_sha256=sha256(derived).hexdigest(),
        removed_futures_row_ids=source_ids,original_preserved=True)


def monthly_preview(service,session,draft,*,forex_applicable,crypto_applicable,futures_applicable=True):
    if type(futures_applicable) is not bool:raise ValueError('Explicit futures applicability required')
    if not futures_applicable and has_futures(session,draft):raise ValueError('Saved futures activity or confirmations require Futures to be included')
    futures=connected_preview(service,session,draft) if futures_applicable else None
    uploads=restore_uploads(session.drafts,draft.id)
    findings=[]
    if futures is not None and not futures['closing_positions_confirmed_by_owner']:
        findings.append('Save the owner confirmation of no ending futures positions.')
    with TemporaryDirectory(prefix='campaigniq-monthly-futures-preview-') as temporary:
        root=Path(temporary)
        supplied=stage_monthly_uploads(upload_dir=root,uploads=uploads)
        identities=service._identity_validations(session,uploads,supplied,draft=draft)
        findings.extend(v.message for v in identities if not v.valid)
        if HISTORY not in uploads:raise ValueError('Save the Trade History before reviewing a trading month')
        original=uploads[HISTORY].getvalue()
        history_path=root/'derived-securities-history.csv'
        if futures is not None:
            lineage=project_history(original,futures,history_path)
        else:
            history_path.write_bytes(original)
            lineage=dict(format='campaigniq.original_monthly_history_lineage',version=1,original_sha256=sha256(original).hexdigest(),original_preserved=True)
        # Identity checks use original source bytes. This derivative is explicitly
        # labelled and is not saved as an original or sent to publication.
        supplied[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY]=root/'derived-securities-history.csv'
        approved_forex=None
        from campaigniq.account_forex_financial_policy import load_receipt as forex_receipt
        if forex_receipt(session,draft) is not None:
            if not forex_applicable:raise ValueError('Approved Forex cannot be omitted')
            from campaigniq.account_forex_generation import prepare as prepare_forex
            approved_forex=prepare_forex(service,session,draft,(root/'derived-securities-history.csv').read_bytes(),root/'approved-forex-history.csv')
            supplied[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY]=root/'approved-forex-history.csv'
        storage=ReadOnlyArtifacts(session.artifacts)
        prepared=prepare_monthly_import(draft.year,draft.month,authoritative_state_root=root/'state',
            supplied_inputs=supplied,artifact_storage=storage,
            forex_applicable=forex_applicable,crypto_applicable=crypto_applicable)
        required={requirement.role.value for requirement in prepared.contract.requirements if requirement.required}
        validations=[dict(role=v.role.value,required=v.role.value in required,valid=v.valid,message=v.message) for v in prepared.validations]
        findings.extend(v['message'] for v in validations if v['required'] and not v['valid'])
        # Crypto controls are assessed against the full original, retaining source
        # row identities, rather than assuming that a provisional export is final.
        from campaigniq.importers.thinkorswim.crypto_reader import read_crypto_report
        raw=root/'original-history.csv';raw.write_bytes(original)
        start=date(draft.year,draft.month,1);end=date(draft.year,draft.month,monthrange(draft.year,draft.month)[1])
        crypto=read_crypto_report(raw,period_start=start,period_end=end)
        active=bool(crypto and (crypto['fills'] or any(Decimal(v['quantity']) for v in crypto['holdings_snapshot'].values())
            or Decimal(crypto.get('opening_cash_usd') or '0') or Decimal(crypto.get('last_reported_cash_usd') or '0')
            or any(row['type']!='BAL' and (Decimal(row['amount_usd']) or Decimal(row.get('fee_cash_change_usd') or '0'))
                for row in crypto['cash_ledger'])))
        crypto_ready=not active
        crypto_status=dict(activity_present=active,statement_supplied=MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT in supplied,
            fill_control_reconciled=bool(crypto and crypto['fill_control_reconciled']),
            cash_control_reconciled=bool(crypto and crypto['cash_control_reconciled']),month_end_reconciled=False)
        approved=None
        from campaigniq.account_crypto_statement_sources import load_receipt
        if load_receipt(session,draft) is not None:
            from campaigniq.account_crypto_generation import build_candidate
            approved=build_candidate(service,session,draft)
            crypto_status['approved_source_candidate_sha256']=approved.sha256
            crypto_status['original_discrepancies_resolved']=False
            crypto_ready=crypto_applicable
            if not crypto_applicable:findings.append('Approved crypto cannot be omitted from this month.')
        if active and approved is None:
            if not crypto_applicable:findings.append('Crypto activity is present and cannot be omitted from this month.')
            if not crypto_status['statement_supplied']:findings.append('The separate crypto account statement is still required.')
            if not crypto_status['fill_control_reconciled']:findings.append('Crypto execution and ledger fills do not match.')
            if not crypto_status['cash_control_reconciled']:findings.append('Crypto cash running-balance discrepancies remain unresolved.')
            if crypto_status['statement_supplied'] and crypto_applicable:
                from campaigniq.persistence.crypto_month import build_crypto_month,load_preceding_crypto
                from campaigniq.importers.schwab.crypto_statement_reader import read_crypto_statement,attach_crypto_statement
                try:
                    check=attach_crypto_statement(build_crypto_month(crypto,load_preceding_crypto(storage,start)),
                        read_crypto_statement(supplied[MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT],period_start=start,period_end=end))
                    crypto_status['month_end_reconciled']=check.get('month_end_reconciled') is True
                except ValueError:
                    findings.append('Crypto statement reconciliation failed; no source was repaired.')
                crypto_ready=(crypto_applicable and all(crypto_status[k] for k in ('fill_control_reconciled','cash_control_reconciled','month_end_reconciled')))
                if not crypto_status['month_end_reconciled']:findings.append('Crypto independent month-end controls have not reconciled.')
        inventory=False;mismatches=[]
        if prepared.ready and all(v.valid for v in identities):
            from campaigniq.monthly_import_execution import execute_monthly_import
            outcome=execute_monthly_import(prepared,authoritative_state_root=root/'state',supplied_inputs=supplied,
                artifact_storage=storage,preview_only=True,_approved_forex=approved_forex)
            if outcome.finalized:raise ValueError('Unexpected finalization in monthly read-only preview')
            control=outcome.closing_reconciliation
            inventory=control.reconciled and not control.forex_source_findings
            findings.extend(control.forex_source_findings)
            forex_csv_control=MonthlyInputRole.SCHWAB_FOREX_MONTHLY_STATEMENT not in supplied
            mismatches=[dict(instrument=repr(row.instrument),computed=str(row.computed_quantity),
                statement=str(row.snapshot_quantity),difference=str(row.difference),
                control_source=('Owner-approved daily Forex policy' if approved_forex is not None else 'Forex Transaction Report CSV' if forex_csv_control else 'Verified Forex monthly evidence')
                    if isinstance(row.instrument,ForexPair) else 'Brokerage closing snapshot')
                for row in control.mismatches]
            if forex_csv_control and any(isinstance(row.instrument,ForexPair) for row in control.mismatches):
                findings.append('Forex closing quantities disagree with the Forex Transaction Report CSV. '
                    'Pairs absent from that CSV retain their published opening quantity; this is not a brokerage closing-snapshot position. '
                    'Saved daily Forex source review does not yet replace this financial control.')
            elif any(isinstance(row.instrument,ForexPair) for row in control.mismatches):
                findings.append('Forex closing inventory does not reconcile with verified monthly evidence.')
            if any(not isinstance(row.instrument,ForexPair) for row in control.mismatches):
                findings.append('Securities closing inventory does not reconcile.')
        ready=bool(prepared.ready and all(v.valid for v in identities) and inventory and crypto_ready
            and (futures is None or futures['closing_positions_confirmed_by_owner']) and not findings)
        return dict(format='campaigniq.futures_monthly_readonly_preview',version=1,account_id=session.account.account_id,
            draft_id=str(draft.id),revision=draft.revision,period=f'{draft.year:04d}-{draft.month:02d}',
            futures=futures,source_lineage=lineage,validations=validations,crypto=crypto_status,
            approved_forex_policy=approved_forex is not None,
            approved_forex_financials=approved_forex.calculation['totals'] if approved_forex is not None else None,
            securities_inventory_reconciled=inventory,inventory_mismatches=mismatches,
            all_source_controls_ready=ready,source_findings=list(dict.fromkeys(findings)),
            publication_authorized=False,publication_integration_ready=False,
            publication_blocker=('Monthly source checks passed. Publication requires the enabled application workflow and separate month confirmation.'
                if ready else 'Month is not ready to publish. Resolve the source and inventory findings shown above; the original sources have been retained.'))
