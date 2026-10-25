"""Period-scoped downstream consumption of independently verified basis groups."""
from collections import defaultdict
from decimal import Decimal
import json

from campaigniq.historical_backfill_basis_provenance import key, number
from campaigniq.historical_backfill_publication_package import canonical, digest
from campaigniq.persistence.realized_attribution_store import deserialize_realized_attributions
from campaigniq.domain.option_contract import OptionContract

MONTHS = tuple(f'2026-{m:02}' for m in range(1, 10))
CLOSE_ACTIONS = {'Close / reduce', 'Roll: close', 'Expiration', 'Assignment'}


def balances(rows):
    result = {}
    for row in rows:
        identity = (key(row['instrument']), row['lot_id'])
        if identity in result:
            raise ValueError('Duplicate lot identity within a period.')
        result[identity] = number(row['quantity'])
    return result


def carry_forward(current, edge):
    """Map exact lots or a documented single-parent split; never a global alias."""
    result = dict(current)
    handled = set()
    for group in edge['review_groups']:
        ins = key(group['instrument'])
        before = {(ins, row['lot_id']): number(row['quantity']) for row in group['prior_lots']}
        hits = set(before) & set(current)
        if not hits:
            continue
        if handled & hits or any(current[identity] != before[identity] for identity in hits):
            raise ValueError('Boundary ancestry overlaps or its quantities differ.')
        handled |= hits
        after = {(ins, row['lot_id']): number(row['quantity']) for row in group['published_opening_lots']}
        if group['classification'] in ('exact_lots', 'same_lots_campaign_labels'):
            if before != after:
                raise ValueError('An exact-lot boundary changes identities or quantities.')
        elif group['classification'] == 'single_origin_split_candidate' and len(before) == 1 and hits == set(before):
            if sum(before.values()) != sum(after.values()) or any(q * next(iter(before.values())) <= 0 for q in after.values()):
                raise ValueError('Split descendants do not conserve signed quantity.')
            for identity in hits:
                del result[identity]
            if set(result) & set(after):
                raise ValueError('Split descendant collides with another tracked lot.')
            result.update(after)
        else:
            raise ValueError('Ambiguous downstream basis regrouping cannot be inferred.')
    if handled != set(current):
        raise ValueError('A tracked lot has no reviewed next-period boundary.')
    return result


def trace_group(group, artifacts, boundaries):
    root = group['instrument']; root_key = key(root)
    current = {(root_key, row['lot_id']): number(row['quantity']) for row in group['retained_opening_lots']}
    root_quantity = sum(current.values())
    if root_quantity != number(group['quantity']) or not root_quantity:
        raise ValueError('Verified opening group quantity differs.')
    consumed = defaultdict(Decimal)
    assignments = []
    proofs = []
    allocation_ids = set()
    stock_basis = Decimal(0)
    option_proceeds = Decimal(0)
    option_realized_quantity = Decimal(0)
    for month in MONTHS:
        if month != MONTHS[0] and current:
            current = carry_forward(current, boundaries[month])
        journal = json.loads(artifacts[month + '-position-journal.json'])['entries']
        opening = {}
        for event in journal:
            if event['action'] == 'Carried position':
                ins = key(event['instrument'])
                for ref in event.get('opening_refs', []):
                    identity = (ins, ref['lot_id'])
                    if identity in opening:
                        raise ValueError('Duplicate carried-lot reference.')
                    opening[identity] = number(ref['quantity'])
        if any(opening.get(identity) != q for identity, q in current.items()):
            raise ValueError('Tracked opening differs from the actual period journal: ' + month)
        transfers = set()
        month_ids = set(current)
        month_consumption = defaultdict(Decimal)
        for index, event in enumerate(journal):
            if event['action'] not in CLOSE_ACTIONS or index in transfers:
                continue
            ins = key(event['instrument'])
            hits = [(ref, (ins, ref['lot_id'])) for ref in event['lots'] if (ins, ref['lot_id']) in current]
            if not hits:
                continue
            if sum((number(ref['quantity']) for ref in event['lots']), Decimal(0)) != abs(number(event['quantity_change'])):
                raise ValueError('Journal event quantity differs from its consumed lot references.')
            for ref, identity in hits:
                q = number(ref['quantity']); before = current[identity]
                if q <= 0 or q > abs(before) or number(event['quantity_change']) * before >= 0:
                    raise ValueError('Closure overconsumes a tracked lot or reverses direction.')
                remaining = before - (q if before > 0 else -q)
                if remaining:
                    current[identity] = remaining
                else:
                    del current[identity]
                if event['action'] == 'Assignment' and event['instrument']['type'] == 'option':
                    contract = event['instrument']
                    if contract['option_type'] != 'PUT' or before >= 0:
                        raise ValueError('This assignment requires a separately verified basis-transfer method.')
                    delivery = [(i, e) for i, e in enumerate(journal) if e['action'] == 'Assignment'
                        and e['occurred_at'] == event['occurred_at'] and e.get('campaign_id') == event.get('campaign_id')
                        and e['instrument'] == dict(type='instrument', symbol=contract['underlying'])]
                    if len(delivery) != 1 or delivery[0][0] in transfers:
                        raise ValueError('Assigned put lacks a unique, unused stock delivery.')
                    delivery_index, stock = delivery[0]
                    # A delivery shared with unrelated option lots would require
                    # unsupported per-fill basis allocation.
                    if len(hits) != len(event['lots']) or number(stock['quantity_change']) != 100 * sum((number(r['quantity']) for r, _ in hits), Decimal(0)):
                        raise ValueError('Assignment delivery includes unverified or inconsistent quantities.')
                    transfers.add(delivery_index)
                    shares = sum((number(r['quantity']) for r in stock['lots']), Decimal(0))
                    if shares != number(stock['quantity_change']):
                        raise ValueError('Assignment stock lot quantities differ.')
                    for ref_stock in stock['lots']:
                        child = (key(stock['instrument']), ref_stock['lot_id'])
                        if child in current:
                            raise ValueError('Assignment stock identity collides with an existing lot.')
                        current[child] = number(ref_stock['quantity'])
                        month_ids.add(child)
                    assignments.append(dict(month=month, option_event_index=index, stock_event_index=delivery_index,
                        option_quantity=str(q), share_quantity=str(100 * q), strike=str(contract['strike']),
                        source_option_lot=ref['lot_id'], delivered_stock_lots=stock['lots']))
                else:
                    month_consumption[identity] += q
        for identity, q in month_consumption.items():
            consumed[(month, *identity)] += q
        # The ending authoritative lot book must retain precisely the unconsumed
        # quantity of each tracked lot, independently of realized money records.
        ending = balances(json.loads(artifacts[month + '-lot-book.json'])['lots'])
        if any(ending.get(identity, Decimal(0)) != current.get(identity, Decimal(0)) for identity in month_ids):
            raise ValueError('Tracked remaining quantity differs from the ending book: ' + month)
        attrs = deserialize_realized_attributions(artifacts[month + '-realized-attributions.json']).attributions
        matched = defaultdict(Decimal)
        raw_records = json.loads(artifacts[month + '-realized-attributions.json'])['attributions']
        for record_index, attr in enumerate(attrs):
            ins = key(raw_records[record_index]['record']['instrument'])
            hits = [(i, allocation) for i, allocation in enumerate(attr.allocations) if (ins, allocation.lot_id) in month_consumption]
            if not hits:
                continue
            if not attr.basis_reconciled or not attr.gain_loss_reconciled:
                raise ValueError('Consumed broker record does not reconcile.')
            record_quantity = sum((number(a.quantity) for _, a in hits), Decimal(0))
            if isinstance(attr.record.instrument, OptionContract):
                if root['type'] != 'option' or record_quantity != attr.record.quantity or len(hits) != len(attr.allocations):
                    raise ValueError('A short-option broker record mixes independent basis groups.')
                option_proceeds += number(attr.record.proceeds)
                option_realized_quantity += record_quantity
            else:
                stock_basis += sum((number(a.broker_basis) for _, a in hits), Decimal(0))
            for allocation_index, allocation in hits:
                aid = (month, record_index, allocation_index)
                if aid in allocation_ids:
                    raise ValueError('A broker allocation was consumed twice.')
                allocation_ids.add(aid)
                matched[(ins, allocation.lot_id)] += number(allocation.quantity)
                proofs.append(dict(month=month, record_index=record_index, allocation_index=allocation_index,
                    lot_id=allocation.lot_id, instrument=ins, quantity=str(allocation.quantity),
                    broker_basis=str(allocation.broker_basis), source_artifact=month + '-realized-attributions.json'))
        if dict(matched) != dict(month_consumption):
            raise ValueError('Journal closures and broker allocation quantities differ: ' + month)
    if current:
        raise ValueError('Year-end group is not fully consumed by the audit horizon.')
    basis = number(group['aggregate_basis_total'])
    if root['type'] != 'option':
        expected, actual, method = basis, stock_basis, 'stock_basis_consumed'
    elif assignments:
        if option_realized_quantity or sum((number(a['option_quantity']) for a in assignments), Decimal(0)) != abs(root_quantity):
            raise ValueError('Mixed option close/assignment paths require separately evidenced premium allocation.')
        exercise = sum((number(a['share_quantity']) * number(a['strike']) for a in assignments), Decimal(0))
        expected, actual, method = exercise + basis, stock_basis, 'assigned_put_basis_transferred_to_stock'
    else:
        if option_realized_quantity != abs(root_quantity):
            raise ValueError('Short-option realized quantities differ from opening group.')
        expected, actual, method = -basis, option_proceeds, 'short_option_opening_proceeds_consumed'
    if expected != actual:
        raise ValueError('Downstream broker monetary consumption differs from verified opening basis.')
    return dict(instrument=root, opening_quantity=str(root_quantity), opening_basis=str(basis),
        method=method, expected_consumption_usd=str(expected), actual_consumption_usd=str(actual),
        complete_quantity_and_monetary_chain_verified=True, assignments=assignments,
        realized_allocations=proofs, allocation_ids=sorted(allocation_ids), per_fill_basis_allocation_inferred=False)


def verify_consumption(provenance, boundary_plan, artifacts):
    if provenance.get('format') != 'campaigniq.aggregate_basis_provenance' or provenance.get('version') != 1 or provenance.get('aggregate_basis_provenance_verified') is not True:
        raise ValueError('Independently verified basis provenance is required.')
    if provenance['boundary_plan_sha256'] != digest(canonical(boundary_plan)) or provenance['capture_sha256'] != boundary_plan['capture_sha256']:
        raise ValueError('Basis provenance and boundary evidence differ.')
    for artifact, checksum in provenance['source_artifact_sha256'].items():
        if digest(artifacts[artifact].encode()) != checksum:
            raise ValueError('Basis provenance source changed.')
    boundaries = {edge['month']: edge for edge in boundary_plan['boundaries']}
    rows = []; used = set()
    for group in provenance['positions']:
        row = trace_group(group, artifacts, boundaries)
        identities = {tuple(a) for a in row.pop('allocation_ids')}
        if used & identities:
            raise ValueError('A broker allocation belongs to multiple opening basis groups.')
        used |= identities; rows.append(row)
    if len(rows) != provenance['position_count'] or not rows:
        raise ValueError('Basis group coverage differs.')
    sources = {f'{month}-{suffix}.json' for month in MONTHS for suffix in ('position-journal', 'lot-book', 'realized-attributions')}
    return dict(format='campaigniq.downstream_basis_consumption', version=1,
        capture_sha256=provenance['capture_sha256'], basis_provenance_sha256=digest(canonical(provenance)),
        source_artifact_sha256={k: digest(artifacts[k].encode()) for k in sorted(sources)},
        positions=rows, position_count=len(rows), consumed_broker_allocation_count=len(used),
        all_year_end_basis_groups_consumed_exactly_once=True, per_fill_basis_allocation_inferred=False,
        original_records_modified=False, full_portfolio_continuity_verified=False, publication_authorized=False)


def main():
    import argparse
    from datetime import datetime, timezone
    import os
    from pathlib import Path
    import subprocess
    import uuid
    from campaigniq.historical_backfill_basis_provenance import build_provenance, retained_texts
    from campaigniq.historical_backfill_restored_publisher import load_inputs
    from campaigniq.historical_backfill_correction_rehearsal import verified_preview
    from campaigniq.importers.schwab.realized_gain_loss_reader import read_realized_gain_loss_section
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('package', 'capture', 'basis-evidence', 'basis-report', 'correction-preview', 'april-realized-report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--development-only', action='store_true', required=True)
    args = parser.parse_args()
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        parser.error('Run downstream basis verification offline locally.')
    paths = {name: getattr(args, name.replace('-', '_')).expanduser() for name in
             ('package', 'capture', 'basis-evidence', 'basis-report', 'correction-preview', 'april-realized-report')}
    required = [paths[name] for name in paths if name != 'package']
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        parser.error('Required saved input is missing: ' + ', '.join(missing))
    package, bundle, candidates = load_inputs(paths['package'], paths['capture'])
    artifacts = bundle['artifacts'] | candidates
    provenance = build_provenance(package['boundary_plan'], artifacts,
        json.loads(paths['basis-evidence'].read_text()), retained_texts(bundle))
    if provenance != json.loads(paths['basis-report'].read_text()):
        parser.error('Saved basis report differs from independently rebuilt source evidence.')
    preview = json.loads(paths['correction-preview'].read_text())
    pdf = paths['april-realized-report'].read_bytes()
    if digest(pdf) != preview['april_source_pdf_sha256']:
        parser.error('Reviewed April PDF checksum differs.')
    text = subprocess.run(['pdftotext', '-layout', str(paths['april-realized-report']), '-'],
        check=True, capture_output=True, text=True, timeout=60).stdout
    proposals = {k: paths['correction-preview'].with_name(k).read_text() for k in preview['proposed_artifact_sha256']}
    corrections = verified_preview(bundle['artifacts'], read_realized_gain_loss_section(text.splitlines()), preview, proposals, pdf)
    report = verify_consumption(provenance, package['boundary_plan'], artifacts | corrections)
    report.update(correction_preview_sha256=digest(paths['correction-preview'].read_bytes()),
        proposed_correction_sha256={k: digest(v.encode()) for k, v in corrections.items()},
        production_connection_made=False, correction_scope='Previously reviewed proposals, applied in memory only.')
    output = Path.home() / 'Downloads' / ('CampaignIQ-basis-consumption-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    path = output / 'basis-consumption.json'
    with path.open('xb') as stream:
        os.chmod(path, 0o600); stream.write(canonical(report) + b'\n')
    print('Downstream year-end basis groups verified:', report['position_count'])
    print('Broker allocations consumed exactly once:', report['consumed_broker_allocation_count'])
    print('Quantity and monetary conservation: PASS')
    print('Report:', path)
    print('Offline candidate verification only. Corrections remain proposals; no records changed or published.')


if __name__ == '__main__':
    main()
