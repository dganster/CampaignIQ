"""Development-only whole-year preview over an immutable workspace capture.

No publishing API is called. A successful inventory check is not authorization.
"""
import base64
import calendar
from dataclasses import replace, asdict
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq.historical_backfill_opening_provenance import trace_opening_provenance
from campaigniq.historical_backfill_bundle import validate, encode
from campaigniq.historical_backfill_draft_preview import _FrozenStorage, _prepare_uploads
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_draft_bridge import RestoredUpload, SUPPORTED_UPLOAD_KEYS
from campaigniq.import_preflight import prepare_monthly_import
from campaigniq.monthly_import_execution import execute_monthly_import
from campaigniq.pdf_text import extract_pdf_text
from campaigniq.persistence.lot_book_store import serialize_lot_book, deserialize_lot_book, _deserialize_instrument, _serialize_instrument
from campaigniq.persistence.monthly_publication import is_month_published_in_storage
from campaigniq.persistence.position_journal import capture_position_journal, deserialize_position_journal


def _number(value):
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError('Non-finite numeric value.')
    # Fixed-point formatting and trimming are exact, independent of Decimal context.
    text = format(number, 'f')
    return (text.rstrip('0').rstrip('.') if '.' in text else text) if number else '0'


def _instrument(instrument):
    data = _serialize_instrument(instrument)
    if data['type'] == 'option':
        data['strike'] = _number(data['strike'])
    return json.dumps(data, sort_keys=True, separators=(',', ':'))


def quantities(book):
    result = {}
    for instrument in book.instruments():
        quantity = sum((lot.quantity for lot in book.lots(instrument)), Decimal(0))
        if not quantity.is_finite():
            raise ValueError('Non-finite position quantity.')
        if quantity:
            key = _instrument(instrument)
            result[key] = result.get(key, Decimal(0)) + quantity
    return result


def differences(left, right):
    return [dict(instrument=k, prior=str(left.get(k, Decimal(0))), following=str(right.get(k, Decimal(0))))
            for k in sorted(left.keys() | right.keys()) if left.get(k, Decimal(0)) != right.get(k, Decimal(0))]


def _state(book, end):
    data = json.loads(serialize_lot_book(period_end=end, lot_book=book))
    for lot in data['lots']:
        lot['quantity'] = _number(lot['quantity'])
        if lot['basis_total'] is not None:
            lot['basis_total'] = _number(lot['basis_total'])
        lot['instrument'] = json.loads(_instrument(_deserialize_instrument(lot['instrument'])))
    data['lots'].sort(key=lambda lot: (json.dumps(lot['instrument'], sort_keys=True), lot['lot_id']))
    return json.dumps(data, sort_keys=True, separators=(',', ':'))


def _state_changes(prior, following, end):
    a, b = (json.loads(_state(book, end)) for book in (prior, following))
    result = []
    if a['next_id'] != b['next_id']:
        result.append(dict(field='next_id', prior=a['next_id'], following=b['next_id']))
    left = {lot['lot_id']: lot for lot in a['lots']}
    right = {lot['lot_id']: lot for lot in b['lots']}
    if len(left) != len(a['lots']) or len(right) != len(b['lots']):
        raise ValueError('Duplicate lot identities in carried state.')
    for lid in sorted(left.keys() | right.keys()):
        if lid not in left or lid not in right:
            result.append(dict(lot_id=lid, field='lot', prior=left.get(lid), following=right.get(lid)))
        else:
            for field in left[lid]:
                if left[lid][field] != right[lid][field]:
                    result.append(dict(lot_id=lid, field=field, prior=left[lid][field], following=right[lid][field]))
    return result


def _uploads(documents, root):
    supported = {getattr(role, 'value', role): role for role in SUPPORTED_UPLOAD_KEYS}
    unknown = sorted({doc['role'] for doc in documents} - supported.keys())
    if unknown:
        raise ValueError('Unsupported saved document roles: ' + ', '.join(unknown))
    uploads = {supported[d['role']]: RestoredUpload(d['filename'], d['media_type'], base64.b64decode(d['content_base64']))
               for d in documents}
    supplied = _prepare_uploads(upload_dir=root, uploads=uploads)
    role = MonthlyInputRole.SCHWAB_FOREX_MONTHLY_STATEMENT
    if role in uploads:
        upload = uploads[role]
        path = root / ('forex_monthly' + Path(upload.name).suffix)
        path.write_bytes(upload.getvalue())
        if path.suffix.lower() == '.pdf':
            path = extract_pdf_text(path, root / 'forex_monthly.txt')
        supplied[role] = path
    return supplied


def _execution_report(execution, start):
    if execution.finalized:
        raise RuntimeError('Preview unexpectedly finalized.')
    capture_position_journal(execution.result, start)  # independently replay exact lots
    reconciliation = execution.closing_reconciliation
    blockers = []
    if not reconciliation.reconciled:
        blockers.append('Closing inventory does not reconcile.')
    blockers.extend(reconciliation.forex_source_findings)
    controls = {}; deltas = {}
    for name in ('settlement', 'pl_total', 'commission', 'financing'):
        value = getattr(execution, 'forex_' + name + '_control_reconciled')
        controls[name] = value
        delta = getattr(execution, 'forex_' + name + '_control_' + ('deltas_usd' if name == 'financing' else 'delta_usd'))
        deltas[name] = None if delta is None else str(delta) if not isinstance(delta, (tuple, list, dict)) else delta
        if value is False:
            blockers.append('Forex monetary control failed: ' + name)
    boundary = execution.result.boundary_reconstruction
    proven = set(getattr(execution.result, 'observed_fifo_boundary_cases', ()))
    # The new evidence answers the old opening-position question through exact
    # FIFO replay, including partial closures. It does not answer historical
    # campaign-start requirements or certify the original pre-capture history.
    eligible = {req.case_id for req in boundary.historical_requirements
                if req.reason.startswith('Opening position ancestry cannot be established')}
    resolved = proven & eligible
    if (set(boundary.unresolved_campaigns) - resolved
            or any(req.case_id not in resolved for req in boundary.historical_requirements)
            or (boundary.unresolved_positions and not boundary.unresolved_campaigns)):
        blockers.append('Historical opening/campaign evidence remains incomplete.')
    return dict(closing_reconciled=reconciliation.reconciled,
                closing_mismatches=[dict(instrument=repr(m.instrument), computed=str(m.computed_quantity), broker=str(m.snapshot_quantity))
                                    for m in reconciliation.mismatches],
                forex_controls=controls, forex_control_deltas_usd=deltas, forex_resolution=execution.forex_resolution,
                forex_report_controls=_forex_controls(execution.result.forex_transaction_report),
                journal_replay_verified=True, blocking_findings=blockers,
                observed_fifo_boundary_cases=sorted(resolved),
                original_campaign_start_dates_verified=False,
                original_boundary_findings=dict(unresolved_positions=list(boundary.unresolved_positions),
                    unresolved_campaigns=list(boundary.unresolved_campaigns), historical_requirements=[asdict(req) for req in boundary.historical_requirements]))


def _forex_controls(report):
    if report is None:
        return None
    return dict(period_start=report.period_start.isoformat(), period_end=report.period_end.isoformat(),
                mtd_settled_pl_usd=str(report.mtd_settled_pl_usd), settlement_row_total_usd=str(report.settlement_pl_usd),
                pl_total_usd=None if report.pl_total_usd is None else str(report.pl_total_usd),
                settlement_count=len(report.settlements), source_policy=report.source_policy,
                pending_settlement_pl_usd=str(report.pending_settlement_pl_usd))


def downstream(storage, december, *, opening_snapshot_text=None):
    """Compare actual published journal openings, never month-end as opening."""
    rows = []
    prior = december
    for month in range(1, 10):
        start = date(2026, month, 1)
        end = date(2026, month, calendar.monthrange(2026, month)[1])
        key = f'{end:%Y-%m}'
        row = dict(month=key, quantity_continuity_verified=False, identity_continuity_verified=False,
                   basis_continuity_verified=False, blocking_findings=[])
        try:
            if not is_month_published_in_storage(storage, period_end=end):
                raise ValueError('Protected month is not published.')
            closing = deserialize_lot_book(storage.read_text(key + '-lot-book.json'))
            if closing.period_end != end:
                raise ValueError('Published closing period differs.')
            entries = deserialize_position_journal(storage.read_text(key + '-position-journal.json'), period_start=start, period_end=end)
            opening = {}; identities = []
            for item in entries:
                if item['action'] != 'Carried position':
                    continue
                instrument = _instrument(_deserialize_instrument(item['instrument']))
                quantity = Decimal(item['quantity_change'])
                opening[instrument] = opening.get(instrument, Decimal(0)) + quantity
                refs = item.get('opening_refs')
                if not isinstance(refs, list) or sum((Decimal(r['quantity']) for r in refs), Decimal(0)) != quantity:
                    raise ValueError('Published journal lacks consistent opening lot references.')
                for ref in refs:
                    q = Decimal(ref['quantity'])
                    if not q.is_finite():
                        raise ValueError('Invalid journal opening quantity.')
                    identities.append((instrument, ref['lot_id'], q, ref['opened_at'], item['campaign_id'] or ''))
            opening = {k: v for k, v in opening.items() if v}
            if prior is None:
                raise ValueError('Previous closing state is unavailable.')
            row['quantity_differences'] = differences(quantities(prior), opening)
            row['quantity_continuity_verified'] = not row['quantity_differences']
            expected = [(_instrument(lot.instrument), lot.lot_id, lot.quantity, lot.opened_at.isoformat(), lot.campaign_id or '')
                        for instrument in prior.instruments() for lot in prior.lots(instrument)]
            row['identity_continuity_verified'] = sorted(expected) == sorted(identities)
            row['unmatched_prior_lot_identities'] = sorted(set(expected) - set(identities))
            row['unmatched_published_opening_identities'] = sorted(set(identities) - set(expected))
            if not row['quantity_continuity_verified']:
                row['blocking_findings'].append('Published opening quantities differ from previous closing.')
            if not row['identity_continuity_verified']:
                row['blocking_findings'].append('Published opening lot/campaign identities differ; reviewed identity mapping required.')
            row['blocking_findings'].append('Published journal does not preserve opening basis; basis continuity is unverified.')
            try:
                from campaigniq.historical_backfill_downstream_audit import audit_boundary
                row['mapping_and_basis_audit'] = audit_boundary(prior, entries,
                    previous_end=start - date.resolution,
                    opening_snapshot_text=opening_snapshot_text if month == 1 else None,
                    provenance_text=storage.read_text(key + '-import-provenance.json')
                        if month == 1 and opening_snapshot_text is not None else None)
            except (ValueError, KeyError, TypeError) as error:
                row['mapping_and_basis_audit'] = dict(verified=False, error=str(error), publication_authorized=False)
                row['blocking_findings'].append('Downstream mapping/basis audit failed: ' + str(error))
        except Exception as error:
            row['blocking_findings'].append(type(error).__name__ + ': ' + str(error))
        # A missing edge must not conceal checks between later published months.
        try:
            persisted = deserialize_lot_book(storage.read_text(key + '-lot-book.json'))
            prior = persisted.lot_book if persisted.period_end == end and is_month_published_in_storage(storage, period_end=end) else None
        except Exception:
            prior = None
        rows.append(row)
    return rows


def run_preflight(bundle, *, forex_not_applicable=(), crypto_not_applicable=(), forex_monthly_evidence=None, campaign_continuity=False, progress=None, candidate_collector=None):
    validate(bundle)
    if candidate_collector is not None:
        import os
        if not campaign_continuity or os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
            raise ValueError('Candidate collection requires local development continuity.')
    if type(campaign_continuity) is not bool:
        raise ValueError('Campaign continuity must be an explicit boolean.')
    if campaign_continuity:
        import os
        if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
            raise ValueError('Development campaign continuity must run locally, never on Render.')
    allowed = {f'2025-{month:02}' for month in range(1, 13)}
    if not set(forex_not_applicable) <= allowed or not set(crypto_not_applicable) <= allowed:
        raise ValueError('Applicability declarations must name 2025 months.')
    supplements = dict(forex_monthly_evidence or {})
    if not supplements.keys() <= allowed:
        raise ValueError('Supplemental statements must name 2025 months.')
    supplemental_manifest = {}
    for month, upload in supplements.items():
        if (not isinstance(upload, RestoredUpload) or not isinstance(upload.content, bytes)
                or not 0 < len(upload.content) <= 20*1024*1024 or Path(upload.name).suffix.lower() not in ('.pdf', '.txt')):
            raise ValueError('Supplemental statements require nonempty PDF/text upload bytes, at most 20 MB.')
        supplemental_manifest[month] = dict(filename=Path(upload.name).name, sha256=sha256(upload.content).hexdigest(), byte_size=len(upload.content))
    before = sha256(encode(bundle)).hexdigest()
    storage = _FrozenStorage(bundle)
    drafts = {(d['year'], d['month']): d for d in bundle['drafts'] if d['state'] == 'draft'}
    rows = []; carried = None; chain = True; identity_chain = True; provenance_closing = {}; opening_snapshot_text = None
    for month in range(1, 13):
        key = f'2025-{month:02}'
        draft = drafts.get((2025, month))
        row = dict(month=key, draft_id=None, revision=None, documents=[],
                   independent_preview=None, continuous_preview=None, blocking_findings=[])
        next_carried = None
        try:
            if draft is None:
                raise ValueError('No saved active draft.')
            row.update(draft_id=draft['id'], revision=draft['revision'])
            docs = [d for d in bundle['documents'] if d['draft_id'] == draft['id'] and not d['superseded']]
            row['documents'] = [{k: d[k] for k in ('id', 'role', 'filename', 'revision', 'sha256')} for d in docs]
            with TemporaryDirectory(prefix='campaigniq-year-preflight-') as directory:
                root = Path(directory)
                supplied = _uploads(docs, root)
                if key in supplements:
                    upload = supplements[key]
                    role = MonthlyInputRole.SCHWAB_FOREX_MONTHLY_STATEMENT
                    saved = [d for d in docs if d['role'] == role.value]
                    if saved and base64.b64decode(saved[0]['content_base64']) != upload.content:
                        raise ValueError('Supplemental monthly statement conflicts with the saved draft; original draft was preserved.')
                    if not saved:
                        path = root / ('supplemental_forex_monthly' + Path(upload.name).suffix)
                        path.write_bytes(upload.content)
                        if path.suffix.lower() == '.pdf':
                            path = extract_pdf_text(path, root / 'supplemental_forex_monthly.txt')
                        supplied[role] = path
                        row['blocking_findings'].append('Supplemental monthly statement is not in the saved draft; upload it before publication.')
                    row['supplemental_monthly_evidence'] = supplemental_manifest[key]
                prepared_hashes = {r.value: sha256(p.read_bytes()).hexdigest() for r, p in supplied.items()}
                preflight = prepare_monthly_import(2025, month, authoritative_state_root=root / 'state',
                    supplied_inputs=supplied, artifact_storage=storage,
                    forex_applicable=key not in forex_not_applicable, crypto_applicable=key not in crypto_not_applicable)
                row['validations'] = [dict(role=v.role.value, valid=v.valid, message=v.message) for v in preflight.validations]
                if not preflight.ready or preflight.opening_lot_book is None:
                    raise ValueError('Required inputs or opening inventory are not ready; see validations.')
                def execute(prepared, *, preserve=False, origins=None):
                    return execute_monthly_import(prepared, authoritative_state_root=root / 'state', supplied_inputs=supplied,
                                                  artifact_storage=storage, preview_only=True,
                                                  **({'development_continuity': True, **({'development_opening_origins': origins} if origins is not None else {})} if preserve else {}))
                independent = execute(preflight)
                row['independent_preview'] = _execution_report(independent, preflight.contract.period_start)
                row['blocking_findings'].extend(row['independent_preview']['blocking_findings'])
                if month == 1:
                    continuous = independent
                    row['campaign_continuity'] = dict(mode='initial_evidenced_anchor', enabled=campaign_continuity,
                        opening_unknown_ancestry=[dict(lot_id=lot.lot_id, instrument=_instrument(lot.instrument), quantity=str(lot.quantity))
                            for instrument in independent.result.opening_lot_book.instruments()
                            for lot in independent.result.opening_lot_book.lots(instrument) if lot.campaign_id is None])
                elif carried is not None:
                    row['opening_quantity_differences'] = differences(quantities(carried), quantities(independent.result.opening_lot_book))
                    if row['opening_quantity_differences']:
                        row['blocking_findings'].append('Previous closing and independently evidenced opening quantities differ.')
                        identity_chain = False
                    prepared = replace(preflight, opening_state=None, bootstrap_opening_lot_book=carried.clone())
                    row['campaign_continuity'] = dict(enabled=campaign_continuity, mode='legacy_diagnostic',
                        input_connected_to_identity_chain=identity_chain and campaign_continuity,
                        connected_to_verified_identity_chain=identity_chain and campaign_continuity,
                        identity_preserving_replay_passed=False, blocking_findings=[])
                    if campaign_continuity:
                        try:
                            from campaigniq.historical_backfill_origin_policy import bind_opening_origins
                            origins = bind_opening_origins(carried, period_end=preflight.contract.period_start - date.resolution,
                                receipts=provenance_closing, documents=bundle['documents'])
                            row['documented_opening_origin_policy'] = dict(asdict(origins), original_campaign_starts_verified=False)
                            continuous = execute(prepared, preserve=True, origins=origins)
                            row['campaign_continuity'].update(mode='identity_preserving', identity_preserving_replay_passed=True)
                            row['campaign_continuity']['trade_links'] = [asdict(link) for link in getattr(continuous.result, 'campaign_identity_links', ())]
                        except ValueError as error:
                            # Retain the strict failure and continue quantity diagnostics.
                            # The fallback is explicitly not an identity continuity pass.
                            finding = 'Identity-preserving campaign replay blocked: ' + str(error)
                            row['campaign_continuity'].update(mode='legacy_quantity_fallback', blocking_findings=[finding],
                                connected_to_verified_identity_chain=False)
                            row['campaign_continuity']['trade_links'] = [asdict(link) for link in getattr(error, 'trade_bindings', ())]
                            row['blocking_findings'].append(finding)
                            identity_chain = False
                            continuous = execute(prepared)
                    else:
                        continuous = execute(prepared)
                    previous_end = preflight.contract.period_start - date.resolution
                    row['opening_state_changes'] = _state_changes(carried, continuous.result.opening_lot_book, previous_end)
                    row['exact_opening_state_carried'] = not row['opening_state_changes']
                    if not row['exact_opening_state_carried']:
                        row['blocking_findings'].append('Pipeline changed carried opening state; provenance/basis review required.')
                        identity_chain = False
                        if campaign_continuity:
                            row['campaign_continuity']['identity_preserving_replay_passed'] = False
                            row['campaign_continuity']['connected_to_verified_identity_chain'] = False
                else:
                    raise ValueError('Continuous replay unavailable because the previous draft did not pass.')
                provenance = None
                try:
                    provenance = trace_opening_provenance(continuous.result,
                        period_start=preflight.contract.period_start, period_end=preflight.contract.period_end,
                        documents=docs, previous=provenance_closing)
                    row['opening_provenance'] = provenance
                except ValueError as error:
                    row['opening_provenance'] = dict(verified=False, error=str(error), publication_authorized=False)
                    row['blocking_findings'].append('Opening provenance replay failed: ' + str(error))
                    identity_chain = False
                row['continuous_preview'] = _execution_report(continuous, preflight.contract.period_start)
                row['blocking_findings'].extend(row['continuous_preview']['blocking_findings'])
                row['closing_quantity_differences'] = differences(quantities(continuous.result.ending_lot_book), quantities(independent.result.ending_lot_book))
                if row['closing_quantity_differences']:
                    row['blocking_findings'].append('Continuous closing differs from the independent monthly preview.')
                    identity_chain = False
                if not row['independent_preview']['closing_reconciled'] or not row['continuous_preview']['closing_reconciled']:
                    identity_chain = False
                if prepared_hashes != {r.value: sha256(p.read_bytes()).hexdigest() for r, p in supplied.items()}:
                    raise ValueError('Prepared source bytes changed during preview.')
                row['prepared_source_sha256'] = prepared_hashes
                if month == 12 and MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT in supplied:
                    opening_snapshot_text = supplied[MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT].read_text(encoding='utf-8')
                row['closing_state_sha256'] = sha256(_state(continuous.result.ending_lot_book, preflight.contract.period_end).encode()).hexdigest()
                # Continue diagnostic position replay through financial/identity
                # findings, retaining every blocker. No missing or mismatched
                # quantity edge is reseeded, approved, or called fully verified.
                if (row['independent_preview']['closing_reconciled'] and row['continuous_preview']['closing_reconciled']
                        and not row.get('opening_quantity_differences') and not row['closing_quantity_differences']):
                    next_carried = continuous.result.ending_lot_book.clone()
                    provenance_closing = provenance['closing'] if provenance is not None else {}
                    if candidate_collector is not None and not row['blocking_findings']:
                        candidate_collector.collect(preflight,continuous,supplied)
        except Exception as error:
            row['blocking_findings'].append(type(error).__name__ + ': ' + str(error))
        if next_carried is None:
            provenance_closing = {}
        carried = next_carried
        chain = chain and carried is not None and not row['blocking_findings']
        if carried is None:
            identity_chain = False
        row['blocking_findings'] = list(dict.fromkeys(row['blocking_findings']))
        rows.append(row)
        if progress:
            progress(row)
    protected = {k: sha256(v.encode()).hexdigest() for k, v in bundle['artifacts'].items()
                 if len(k) >= 7 and k[:4].isdigit() and int(k[:4]) >= 2026}
    edges = downstream(storage, carried, opening_snapshot_text=opening_snapshot_text)
    if sha256(encode(bundle)).hexdigest() != before:
        raise RuntimeError('Frozen workspace capture changed.')
    blockers = [r['month'] + ': ' + b for r in rows + edges for b in r['blocking_findings']]
    blockers += ['Physical PostgreSQL restore and rollback have not been tested.',
                 'A separately authorized transactional historical publisher has not been implemented.']
    report = dict(format='campaigniq.whole_year_backfill_preflight', version=3, diagnostic_only=True,
                workspace=bundle['workspace'], captured_at=bundle['captured_at'], capture_sha256=before,
                months=rows, downstream=edges, all_2025_drafts_passed=chain,
                diagnostic_position_replay_completed=carried is not None and all(row['continuous_preview'] is not None for row in rows),
                campaign_continuity_enabled=campaign_continuity,
                identity_preserving_chain_verified=campaign_continuity and identity_chain and carried is not None,
                campaign_continuity_policy='Unknown ancestry and distinct carried campaign merges remain blockers. A labeled legacy fallback verifies quantities only and never restores a broken identity chain.',
                december_boundary_source='Diagnostic continuous replay; unresolved findings do not authorize publication.',
                state_comparison_policy='Exact numeric values and instrument fields; lot IDs, campaign IDs, basis and allocator retained.',
                supplemental_monthly_evidence=supplemental_manifest,
                protected_artifact_sha256=protected, captured_bytes_unchanged=True,
                full_portfolio_continuity_verified=False, publication_authorized=False,
                applicability_declarations=dict(forex_not_applicable=sorted(forex_not_applicable), crypto_not_applicable=sorted(crypto_not_applicable)),
                blocking_findings=blockers,
                limitations=['Workspace artifacts and all draft document versions only; not a full database or persistent-disk backup.',
                             'Read-only snapshot cannot establish that production remained unchanged after capture.',
                             'Crypto reporting and source completeness require independent review.'])
    if report['all_2025_drafts_passed'] and report['identity_preserving_chain_verified']:
        try:
            from campaigniq.historical_backfill_boundary_plan import build_boundary_plan
            plan = build_boundary_plan(bundle,report,carried,opening_snapshot_text=opening_snapshot_text,development_only=True)
            report['boundary_plan'] = plan.to_dict()
            report['boundary_plan_sha256'] = plan.sha256
        except (ValueError,KeyError,TypeError) as error:
            report['boundary_plan'] = dict(status='not_prepared',reason=str(error),publication_authorized=False)
    else:
        report['boundary_plan'] = dict(status='not_prepared',reason='2025 draft and identity checks are incomplete.',publication_authorized=False)
    return report

