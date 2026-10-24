"""Read-only boundary mapping candidates; never authorize aliases or basis repair."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json

from campaigniq.persistence.lot_book_store import _serialize_instrument, serialize_lot_book


def number(value):
    if isinstance(value, (float, bool)):
        raise ValueError('Exact decimal source values are required.')
    value = Decimal(value)
    if not value.is_finite():
        raise ValueError('Non-finite boundary value.')
    if value == 0:
        return '0'
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def instrument_key(instrument):
    data = _serialize_instrument(instrument)
    if data['type'] == 'option':
        data['strike'] = number(data['strike'])
    return json.dumps(data, sort_keys=True, separators=(',', ':'))


def _journal_rows(entries):
    from campaigniq.persistence.lot_book_store import _deserialize_instrument
    rows = []; seen = set()
    for entry in entries:
        if entry['action'] != 'Carried position':
            continue
        refs = entry.get('opening_refs')
        if not isinstance(refs, list) or not refs:
            raise ValueError('Opening lot references are missing.')
        total = Decimal(0)
        for ref in refs:
            lid = ref['lot_id']; q = Decimal(number(ref['quantity']))
            if not isinstance(lid, str) or not lid or lid in seen or not q:
                raise ValueError('Duplicate/empty lot identity or zero opening quantity.')
            seen.add(lid); total += q
            datetime.fromisoformat(ref['opened_at'])
            rows.append(dict(instrument=instrument_key(_deserialize_instrument(entry['instrument'])),
                lot_id=lid, quantity=number(q), opened_at=ref['opened_at'],
                campaign_id=entry.get('campaign_id'), basis_total=None, basis_source=None,
                basis_recorded=False))
        if total != Decimal(number(entry['quantity_change'])):
            raise ValueError('Opening journal reference quantities disagree.')
    return rows


def _book_rows(book):
    rows = []; seen = set()
    for instrument in book.instruments():
        for lot in book.lots(instrument):
            if lot.lot_id in seen:
                raise ValueError('Duplicate prior lot identity.')
            seen.add(lot.lot_id)
            q = number(lot.quantity)
            if Decimal(q) == 0:
                raise ValueError('Zero prior lot quantity.')
            rows.append(dict(instrument=instrument_key(instrument), lot_id=lot.lot_id,
                quantity=q, opened_at=lot.opened_at.isoformat(), campaign_id=lot.campaign_id,
                basis_total=None if lot.basis_total is None else number(lot.basis_total),
                basis_source=lot.basis_source, basis_recorded=lot.basis_total is not None))
    return rows


def _snapshot_basis(text, provenance_text, published, previous_end):
    """Recover retained input facts only after matching the published input hash."""
    result = dict(source_hash_verified=False, opening_basis_recovered=False,
                  rows=[], limitation='No matching retained opening snapshot supplied.')
    if text is None or provenance_text is None:
        return result
    from campaigniq.importers.schwab.position_snapshot_reader import read_position_snapshot_section
    from campaigniq.importers.schwab.position_snapshot import to_lots
    from campaigniq.domain.lot_book import LotBook
    provenance = json.loads(provenance_text)
    if provenance.get('format') != 'campaigniq.monthly_import_provenance' or provenance.get('version') != 1:
        raise ValueError('Invalid published source provenance format.')
    from datetime import timedelta
    if provenance.get('period_start') != (previous_end + timedelta(days=1)).isoformat():
        raise ValueError('Published opening-source provenance has the wrong period.')
    import calendar
    start = previous_end + timedelta(days=1)
    if provenance.get('period_end') != start.replace(day=calendar.monthrange(start.year,start.month)[1]).isoformat():
        raise ValueError('Published source provenance has the wrong period end.')
    sources = [row for row in provenance['inputs'] if row['role'] == 'schwab_opening_position_snapshot']
    digest = sha256(text.encode()).hexdigest()
    if len(sources) != 1 or sources[0]['sha256'] != digest or sources[0]['byte_size'] != len(text.encode()):
        raise ValueError('Retained opening snapshot does not match published source provenance.')
    at = datetime.combine(previous_end, datetime.max.time())
    book = LotBook()
    for lot in to_lots(read_position_snapshot_section(text.splitlines(), snapshot_at=at)):
        book.seed(lot)
    source = _book_rows(book)
    expected = {row['lot_id']: row for row in published if row['lot_id'].startswith('SCHWAB-SNAPSHOT:')}
    if not source or set(expected) != {row['lot_id'] for row in source}:
        raise ValueError('Retained snapshot and published snapshot lot identities differ.')
    for row in source:
        target = expected[row['lot_id']]
        if any(row[key] != target[key] for key in ('instrument', 'quantity', 'opened_at')):
            raise ValueError('Retained snapshot and published opening quantities or dates differ.')
    return dict(source_hash_verified=True, source_sha256=digest,
                opening_basis_recovered=True, rows=source,
                known_basis_lots=sum(row['basis_recorded'] for row in source),
                unknown_basis_lots=sum(not row['basis_recorded'] for row in source),
                limitation='Retained broker input facts only; this does not establish prior replay basis equality or approve campaign mapping.')


def audit_boundary(prior_book, entries, *, previous_end, opening_snapshot_text=None, provenance_text=None):
    """Explain complete opening inventory matches, including exact lot splits.

    Candidates are scoped to one boundary. Aggregate agreement never approves a
    merge, a split allocation between indistinguishable lots, or a cost-basis value.
    Ordinary boundaries require matching origin timestamps. The first January
    snapshot may have observation timestamps instead; that is labeled explicitly.
    """
    from datetime import timedelta
    if (previous_end + timedelta(days=1)).day != 1:
        raise ValueError('A month-end boundary is required.')
    prior = _book_rows(prior_book); published = _journal_rows(entries)
    if any(datetime.fromisoformat(row['opened_at']).date() > previous_end for row in prior + published):
        raise ValueError('Opening lot origin is after the boundary.')
    snapshot_boundary = previous_end.isoformat() == '2025-12-31'
    groups = defaultdict(lambda: [[], []])
    for side, rows in enumerate((prior, published)):
        for row in rows:
            key = (row['instrument'], Decimal(row['quantity']) > 0,
                   '' if snapshot_boundary else row['opened_at'])
            groups[key][side].append(row)
    candidates = []; problems = []; relations = set()
    for key, (left, right) in sorted(groups.items()):
        lq = sum((Decimal(row['quantity']) for row in left), Decimal(0))
        rq = sum((Decimal(row['quantity']) for row in right), Decimal(0))
        complete = bool(left and right) and lq == rq
        if not complete:
            classification = 'unmatched_or_changed_inventory'
            problems.append('Inventory or origin timestamp differs for ' + key[0])
        elif snapshot_boundary:
            classification = 'snapshot_regrouping_candidate'
        elif sorted((r['lot_id'], r['quantity']) for r in left) == sorted((r['lot_id'], r['quantity']) for r in right):
            classification = 'same_lots_campaign_labels' if {r['campaign_id'] for r in left} != {r['campaign_id'] for r in right} else 'exact_lots'
        elif len(left) == 1:
            classification = 'single_origin_split_candidate'
        else:
            classification = 'ambiguous_multiple_origin_regrouping'
            problems.append('Multiple origins require allocation evidence for ' + key[0])
        lc = {row['campaign_id'] for row in left if row['campaign_id']}
        rc = {row['campaign_id'] for row in right if row['campaign_id']}
        if complete and len(lc) == len(rc) == 1:
            relations.add((next(iter(lc)), next(iter(rc))))
        candidates.append(dict(instrument=json.loads(key[0]), classification=classification,
            aggregate_quantity_match=complete, prior_quantity=number(lq), published_quantity=number(rq),
            prior_lots=left, published_opening_lots=right, mapping_approved=False,
            basis_continuity_verified=False))
    forward = defaultdict(set); backward = defaultdict(set)
    for old, new in relations:
        forward[old].add(new); backward[new].add(old)
    conflicts = [dict(kind='one_prior_to_multiple_published_campaigns', campaign_id=cid, targets=sorted(targets))
                 for cid, targets in sorted(forward.items()) if len(targets) > 1]
    conflicts += [dict(kind='multiple_prior_to_one_published_campaign', campaign_id=cid, sources=sorted(sources))
                  for cid, sources in sorted(backward.items()) if len(sources) > 1]
    basis = _snapshot_basis(opening_snapshot_text, provenance_text, published, previous_end)
    if basis['source_hash_verified']:
        snapshot_groups = defaultdict(list)
        for row in basis['rows']:
            snapshot_groups[row['instrument']].append(row)
        for candidate in candidates:
            rows = snapshot_groups.get(json.dumps(candidate['instrument'],sort_keys=True,separators=(',', ':')), [])
            prior_values = [row['basis_total'] for row in candidate['prior_lots']]
            snapshot_values = [row['basis_total'] for row in rows]
            prior_total = number(sum((Decimal(v) for v in prior_values), Decimal(0))) if prior_values and None not in prior_values else None
            snapshot_total = number(sum((Decimal(v) for v in snapshot_values), Decimal(0))) if snapshot_values and None not in snapshot_values else None
            candidate['prior_basis_total'] = prior_total
            candidate['retained_snapshot_basis_total'] = snapshot_total
            candidate['aggregate_basis_values_match'] = prior_total == snapshot_total if prior_total is not None and snapshot_total is not None else None
    return dict(format='campaigniq.downstream_mapping_basis_audit', version=1,
                previous_period_end=previous_end.isoformat(), candidates=candidates,
                candidate_campaign_relations=[dict(prior=a, published=b) for a,b in sorted(relations)],
                campaign_relation_conflicts=conflicts, unresolved_findings=problems,
                prior_state_sha256=sha256(serialize_lot_book(period_end=previous_end, lot_book=prior_book).encode()).hexdigest(),
                journal_opening_rows_sha256=sha256(json.dumps(published,sort_keys=True).encode()).hexdigest(),
                retained_opening_snapshot=basis,
                complete_candidate_inventory_match=not problems,
                basis_continuity_verified=False, mapping_approved=False,
                publication_authorized=False,
                limitations=['Boundary-scoped candidates only; no global campaign aliases or writes.',
                             'Opening journals omit basis. Unknown basis is not zero.',
                             'A matching snapshot observation date is not an acquisition date.'])
