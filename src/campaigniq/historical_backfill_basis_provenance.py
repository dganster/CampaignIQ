"""Source-verified aggregate brokerage basis provenance; never edits lot basis."""
import argparse
import base64
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import uuid

from campaigniq.historical_backfill_publication_package import canonical, digest
from campaigniq.historical_backfill_downstream_audit import instrument_key, _snapshot_basis, _journal_rows, _book_rows
from campaigniq.persistence.lot_book_store import _deserialize_instrument, deserialize_lot_book


def number(value):
    result = Decimal(str(value).replace(',', ''))
    if not result.is_finite():
        raise ValueError('Non-finite basis evidence.')
    return result


def key(instrument):
    return instrument_key(_deserialize_instrument(instrument))


def verify_quote(source, texts):
    checksum = source['statement_pdf_sha256']
    text = texts[checksum]
    lines = text.splitlines()
    first = source['text_line'] - 1
    quote = source['quote']
    if first < 0 or lines[first:first + len(quote.splitlines())] != quote.splitlines():
        raise ValueError('Basis quotation does not match its exact retained PDF text.')
    return quote


def short_sale(source, texts, instrument):
    quote = verify_quote(source, texts)
    block = '\n'.join(quote.splitlines()[:2])
    symbol = instrument['underlying']
    if not re.search(r'Sale\s+Short Sale\s+' + re.escape(symbol) + r'\b', quote.splitlines()[0]):
        raise ValueError('Short-sale underlying differs.')
    expiration = datetime.fromisoformat(instrument['expiration'])
    if expiration.strftime('%m/%d/%Y') not in block and 'EXP ' + expiration.strftime('%m/%d/%y') not in block:
        raise ValueError('Short-sale expiration differs.')
    strike = re.search(r'\$\s*([\d,.]+)', block)
    if strike is None or number(strike[1]) != number(instrument['strike']) or not re.search(r'\b' + instrument['option_type'] + r'\b', block):
        raise ValueError('Short-sale strike or option type differs.')
    columns = re.search(r'\(([\d,.]+)\)\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)\s*$', quote.splitlines()[0])
    if columns is None:
        raise ValueError('Unsupported short-sale transaction columns.')
    quantity, price, fees, net = map(number, columns.groups())
    if quantity <= 0 or price < 0 or fees < 0 or quantity * 100 * price - fees != net:
        raise ValueError('Short-sale net proceeds fail exact arithmetic.')
    for name, value in (('quantity', quantity), ('price', price), ('fees', fees), ('net_proceeds', net)):
        if number(source[name]) != value:
            raise ValueError('Quoted short-sale columns differ from the evidence manifest.')
    return quantity, net


def purchase(source, texts, symbol):
    quote = verify_quote(source, texts)
    if not re.search(r'Purchase\s+' + re.escape(symbol) + r'\b', quote):
        raise ValueError('Stock purchase underlying differs.')
    columns = re.search(r'\s([\d,.]+)\s+([\d,.]+)\s+\(([\d,.]+)\)\s*$', quote)
    if columns is None:
        raise ValueError('Unsupported stock purchase transaction columns.')
    quantity, price, cash = map(number, columns.groups())
    if quantity <= 0 or price < 0 or quantity * price != cash:
        raise ValueError('Stock purchase amount fails exact arithmetic.')
    return quantity, price, cash


def verify_opening_sale(journal, instrument, lot_ids, source):
    openings = [event for event in journal if key(event['instrument']) == key(instrument)
        and event['action'] in ('Open / add', 'Roll: open')
        and any(ref['lot_id'] in lot_ids for ref in event['lots'])]
    quantity = sum((-number(e['quantity_change']) for e in openings), Decimal(0))
    gross = sum((-number(e['quantity_change']) * number(e['price']) * 100 for e in openings), Decimal(0))
    if not openings or any(number(e['quantity_change']) >= 0 for e in openings) or quantity != number(source['quantity']) or gross != number(source['net_proceeds']) + number(source['fees']):
        raise ValueError('Exact retained option opening executions differ from the quoted short sale.')
    return openings


def build_provenance(boundary_plan, artifacts, evidence, texts):
    """Verify each source formula and lot origin. Never allocate aggregate fees."""
    from datetime import date
    if evidence.get('format') != 'campaigniq.basis_evidence_investigation' or evidence.get('version') != 1:
        raise ValueError('Unsupported basis evidence format.')
    if evidence.get('capture_sha256') != boundary_plan['capture_sha256']:
        raise ValueError('Basis evidence belongs to another capture.')
    edges = [e for e in boundary_plan['boundaries'] if e['month'] == '2026-01']
    if len(edges) != 1:
        raise ValueError('Exactly one year-end boundary is required.')
    edge = edges[0]
    snapshot_hash = edge['retained_opening_snapshot']['source_sha256']
    snapshot_texts = [text for text in texts.values() if digest(text.encode()) == snapshot_hash]
    if not snapshot_texts:
        raise ValueError('Retained January opening source text is unavailable.')
    entries = json.loads(artifacts['2026-01-position-journal.json'])['entries']
    snapshot = _snapshot_basis(snapshot_texts[0], artifacts['2026-01-import-provenance.json'], _journal_rows(entries), date(2025, 12, 31))
    if snapshot != edge['retained_opening_snapshot']:
        raise ValueError('Retained opening basis facts differ from the reviewed boundary.')
    manifests = {key(row['instrument']): row for row in evidence['positions']}
    groups = {key(group['instrument']): group for group in edge['review_groups']}
    if len(manifests) != len(evidence['positions']) or len(groups) != len(edge['review_groups']) or manifests.keys() != groups.keys():
        raise ValueError('Basis evidence does not cover each year-end instrument exactly once.')
    journal_keys = [f'2025-{m:02}-position-journal.json' for m in range(1, 13)]
    journal = [row for name in journal_keys for row in json.loads(artifacts[name])['entries']]
    prior_rows = _book_rows(deserialize_lot_book(artifacts['2025-12-lot-book.json']).lot_book)
    if set(groups) != {r['instrument'] for r in prior_rows} or set(groups) != {r['instrument'] for r in snapshot['rows']}:
        raise ValueError('Year-end provenance omits an actual closing or opening instrument.')
    output = []
    for inskey, group in groups.items():
        row = manifests[inskey]
        if row['prior_lots'] != group['prior_lots'] or not group['aggregate_quantity_match']:
            raise ValueError('Year-end lot group differs from reviewed evidence.')
        instrument = group['instrument']; quantity = number(group['prior_quantity'])
        actual_prior = [lot for lot in prior_rows if lot['instrument'] == inskey]
        if sorted(actual_prior, key=lambda lot: lot['lot_id']) != sorted(group['prior_lots'], key=lambda lot: lot['lot_id']):
            raise ValueError('Basis provenance prior lots differ from the actual December closing book.')
        target = number(group['retained_snapshot_basis_total'])
        snapshot_total = sum((number(lot['basis_total']) for lot in snapshot['rows'] if lot['instrument'] == inskey), Decimal(0))
        if target != snapshot_total:
            raise ValueError('Reviewed aggregate differs from the independently parsed opening snapshot.')
        sources = [row['source']] if row.get('source') else []
        origins = []
        if instrument['type'] == 'option':
            if quantity >= 0:
                raise ValueError('Long-option opening evidence requires its own verified method.')
            contracts, net = short_sale(row['source'], texts, instrument)
            if contracts != -quantity:
                raise ValueError('Short-sale quantity differs from the complete year-end group.')
            origins = verify_opening_sale(journal, instrument, {lot['lot_id'] for lot in group['prior_lots']}, row['source'])
            calculated = -net
            method = 'short_option_negative_net_proceeds'
        else:
            ids = {lot['lot_id'] for lot in group['prior_lots']}
            origins = [event for event in journal if key(event['instrument']) == inskey
                       and event['action'] in ('Assignment', 'Open / add')
                       and any(ref['lot_id'] in ids for ref in event['lots'])]
            if not origins or sum((number(e['quantity_change']) for e in origins), Decimal(0)) != quantity:
                raise ValueError('Stock opening journal does not uniquely cover the retained lots.')
            deliveries = row['exercise_or_purchase_evidence']
            purchases = [purchase(source, texts, instrument['symbol']) for source in deliveries]
            sources += deliveries
            if sum((q for q, _, _ in purchases), Decimal(0)) != quantity:
                raise ValueError('Broker purchase quantity differs from retained stock lots.')
            cash = sum((amount for _, _, amount in purchases), Decimal(0))
            if all(e['action'] == 'Assignment' for e in origins):
                contracts = []
                assigned_ids = set()
                for event in origins:
                    options = [e for e in journal if e['action'] == 'Assignment' and e['occurred_at'] == event['occurred_at']
                        and e['campaign_id'] == event['campaign_id'] and e['instrument'].get('underlying') == instrument['symbol']
                        and e['instrument'].get('option_type') == 'PUT'
                        and 100 * number(e['quantity_change']) == number(event['quantity_change'])]
                    if len(options) != 1:
                        raise ValueError('Stock assignment lacks one exact matching put delivery.')
                    contracts.append(options[0]['instrument'])
                    assigned_ids.update(ref['lot_id'] for ref in options[0]['lots'])
                if len({key(c) for c in contracts}) != 1:
                    raise ValueError('Mixed assigned contracts require separately sourced basis groups.')
                contract = contracts[0]
                if cash != quantity * number(contract['strike']) or any(price != number(contract['strike']) for _, price, _ in purchases):
                    raise ValueError('Assigned purchase exercise price differs.')
                count, premium = short_sale(row['source'], texts, contract)
                if count * 100 != quantity:
                    raise ValueError('Assigned put premium does not cover all delivered shares.')
                verify_opening_sale(journal, contract, assigned_ids, row['source'])
                calculated = cash - premium
                method = 'assigned_put_exercise_less_net_premium'
            elif all(e['action'] == 'Open / add' for e in origins) and not row.get('source'):
                journal_cash = sum((number(e['quantity_change']) * number(e['price']) for e in origins), Decimal(0))
                if journal_cash != cash:
                    raise ValueError('Direct stock execution cash differs from the broker purchase.')
                calculated = cash
                method = 'direct_stock_purchase_cash'
            else:
                raise ValueError('Mixed stock origins require separate evidence groups.')
        if calculated != target or number(row['calculated_basis']) != calculated or number(row['snapshot_basis']) != target:
            raise ValueError('Independent source formula differs from the retained broker opening basis.')
        output.append(dict(instrument=instrument, quantity=str(quantity), aggregate_basis_total=str(calculated),
            method=method, source_quotes=sources, stock_origin_events=origins,
            prior_lots=group['prior_lots'], retained_opening_lots=group['published_opening_lots'],
            snapshot_source_sha256=snapshot_hash, exact_aggregate_match=True,
            per_fill_basis_allocation_inferred=False))
    keys = set(journal_keys) | {'2025-12-lot-book.json', '2026-01-position-journal.json', '2026-01-import-provenance.json'}
    return dict(format='campaigniq.aggregate_basis_provenance', version=1,
        capture_sha256=boundary_plan['capture_sha256'], boundary_plan_sha256=digest(canonical(boundary_plan)),
        evidence_manifest_sha256=digest(canonical(evidence)),
        source_artifact_sha256={k: digest(artifacts[k].encode()) for k in sorted(keys)},
        source_pdf_sha256=sorted({s['statement_pdf_sha256'] for r in output for s in r['source_quotes']}),
        positions=output, position_count=len(output), aggregate_basis_provenance_verified=True,
        original_lot_basis_modified=False, per_fill_basis_allocation_inferred=False,
        limitation='Verified year-end aggregate provenance only. Downstream basis consumption and publication require separate checks.',
        full_portfolio_continuity_verified=False, publication_authorized=False)


def retained_texts(bundle):
    from campaigniq.pdf_text import extract_pdf_text
    texts = {}
    with TemporaryDirectory(prefix='campaigniq-basis-sources-') as temp:
        for doc in bundle['documents']:
            if doc['role'] != 'schwab_brokerage_statement':
                continue
            raw = base64.b64decode(doc['content_base64'], validate=True)
            checksum = digest(raw)
            if checksum != doc['sha256']:
                raise ValueError('Retained source PDF checksum differs.')
            if checksum in texts:
                continue
            source = Path(temp) / (checksum + '.pdf'); source.write_bytes(raw)
            texts[checksum] = extract_pdf_text(source, source.with_suffix('.txt')).read_text(encoding='utf-8')
    return texts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('package', 'capture', 'evidence'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--development-only', action='store_true', required=True)
    args = parser.parse_args()
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        parser.error('Run the source provenance check offline locally.')
    from campaigniq.historical_backfill_restored_publisher import load_inputs
    package, bundle, candidates = load_inputs(args.package.expanduser(), args.capture.expanduser())
    report = build_provenance(package['boundary_plan'], bundle['artifacts'] | candidates,
        json.loads(args.evidence.expanduser().read_text()), retained_texts(bundle))
    output = Path.home() / 'Downloads' / ('CampaignIQ-basis-provenance-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    path = output / 'basis-provenance.json'
    with path.open('xb') as stream:
        os.chmod(path, 0o600); stream.write(canonical(report) + b'\n')
    print('Source-verified year-end aggregate basis groups:', report['position_count'])
    print('Exact broker opening basis matches: PASS')
    print('Individual fill basis allocation inferred: False')
    print('Report:', path)
    print('Offline source verification only. No records changed or publication attempted.')


if __name__ == '__main__':
    main()
