"""Period-scoped continuity links and exact-once realized-record accounting.

Links retain source campaign IDs. They are not aliases or publication approval.
"""
from collections import defaultdict
import calendar
from datetime import date
from decimal import Decimal
import json
from campaigniq.historical_backfill_publication_package import canonical, digest
from campaigniq.historical_backfill_downstream_audit import instrument_key, audit_boundary
from campaigniq.persistence.lot_book_store import _deserialize_instrument, deserialize_lot_book
from campaigniq.persistence.position_journal import deserialize_position_journal
from campaigniq.historical_backfill_candidate_payloads import validate_generation
from campaigniq.persistence.realized_attribution_store import deserialize_realized_attributions
from campaigniq.persistence.monthly_publication import PROTOCOL_FILE


def instrument(raw):
    return instrument_key(_deserialize_instrument(raw))


def campaign_node(month,campaign_id):
    # A null/unassigned campaign is not a common identity shared by all lots.
    if not campaign_id:return None
    return (month,campaign_id)


def build_links(boundary_plan,artifacts):
    expected=boundary_plan['existing_artifact_sha256']
    # Correction proposals may change realized records only. All other boundary
    # evidence must remain byte-identical to the independently built plan.
    for key,checksum in expected.items():
        # The publication cutover controls visibility, not lot ancestry. Its
        # authorized transaction change is verified separately by the publisher.
        if key!=PROTOCOL_FILE and not key.endswith('-realized-attributions.json'):
            if key not in artifacts or digest(artifacts[key].encode())!=checksum:
                raise ValueError('Boundary supporting artifact changed: '+key)
    boundaries=list(boundary_plan['boundaries'])
    historical={k:v for k,v in artifacts.items() if k.startswith('2025-')}
    if historical:
        validate_generation(historical)
        prior_edges=[]
        for month in range(2,13):
            previous=date(2025,month-1,calendar.monthrange(2025,month-1)[1])
            start=date(2025,month,1);end=date(2025,month,calendar.monthrange(2025,month)[1])
            prior=deserialize_lot_book(artifacts[f'{previous:%Y-%m}-lot-book.json'])
            entries=deserialize_position_journal(artifacts[f'2025-{month:02}-position-journal.json'],period_start=start,period_end=end)
            audit=audit_boundary(prior.lot_book,entries,previous_end=previous)
            if not audit['complete_candidate_inventory_match']:raise ValueError('Historical candidate boundary differs.')
            prior_edges.append(dict(month=f'2025-{month:02}',review_groups=audit['candidates']))
        boundaries=prior_edges+boundaries
    groups=[];nodes=set();edges=set()
    for boundary in boundaries:
        month=boundary['month']
        year,number=map(int,month.split('-'))
        previous=f'{year-1}-12' if number==1 else f'{year}-{number-1:02}'
        for index,group in enumerate(boundary['review_groups']):
            if group['aggregate_quantity_match'] is not True:
                raise ValueError('Unreconciled boundary quantity.')
            left=group['prior_lots'];right=group['published_opening_lots']
            if not left or not right:raise ValueError('Missing boundary lot references.')
            left_total=sum((Decimal(x['quantity']) for x in left),Decimal(0))
            right_total=sum((Decimal(x['quantity']) for x in right),Decimal(0))
            if not left_total or left_total!=right_total:raise ValueError('Boundary group quantity differs.')
            ins=instrument(group['instrument'])
            if any(x['instrument']!=ins for x in left+right):
                raise ValueError('Boundary group mixes instruments.')
            if len({x['lot_id'] for x in left})!=len(left) or len({x['lot_id'] for x in right})!=len(right):
                raise ValueError('Duplicate lot reference within a boundary side.')
            if month!='2026-01' and len({x['opened_at'] for x in left+right})!=1:
                raise ValueError('Boundary lot origin dates differ.')
            classification=group['classification']
            if classification not in ('snapshot_regrouping_candidate','same_lots_campaign_labels','exact_lots','single_origin_split_candidate'):
                raise ValueError('Ambiguous or unsupported boundary group.')
            if classification=='single_origin_split_candidate' and len(left)!=1:
                raise ValueError('A split requires one documented origin.')
            group_id=month+':'+str(index)+':'+digest(canonical(group))
            # Join through a group, never through a global ID substitution.
            parents={campaign_node(previous,x['campaign_id']) for x in left}-{None}
            children={campaign_node(month,x['campaign_id']) for x in right}-{None}
            nodes.update(parents|children)
            if classification in ('exact_lots','same_lots_campaign_labels'):
                # Equal instrument/date groups can still contain independent
                # campaigns. Match each persistent lot, not every group member.
                targets={x['lot_id']:x for x in right}
                if {x['lot_id'] for x in left}!=set(targets):raise ValueError('Exact-lot identities differ.')
                for origin in left:
                    target=targets[origin['lot_id']]
                    if Decimal(origin['quantity'])!=Decimal(target['quantity']):raise ValueError('Exact-lot quantity differs.')
                    parent=campaign_node(previous,origin['campaign_id']);child=campaign_node(month,target['campaign_id'])
                    intermediary=('boundary_group',group_id+':'+origin['lot_id'])
                    if parent:edges.add((parent,intermediary))
                    if child:edges.add((intermediary,child))
            else:
                for parent in parents:edges.add((parent,('boundary_group',group_id)))
                for child in children:edges.add((('boundary_group',group_id),child))
            groups.append(dict(group_id=group_id,month=month,instrument=group['instrument'],
                quantity=str(left_total),classification=classification,
                prior_lots=json.loads(canonical(left)),published_opening_lots=json.loads(canonical(right)),
                source_group_sha256=digest(canonical(group)),
                relationship='quantity_conserving_group',basis_allocation_inferred=False))
    records=[]
    for key,text in sorted(artifacts.items()):
        if not key.endswith('-realized-attributions.json'):continue
        month=key[:7]
        if month[:4] not in ('2025','2026'):continue
        persisted=deserialize_realized_attributions(text)
        if f'{persisted.period_end:%Y-%m}'!=month:raise ValueError('Realized artifact month differs.')
        for index,attribution in enumerate(persisted.attributions):
            record=attribution.record
            if not attribution.basis_reconciled or attribution.allocated_quantity!=record.quantity:
                raise ValueError('Realized allocations fail quantity or basis conservation.')
            if not attribution.gain_loss_reconciled:raise ValueError('Broker record monetary controls differ.')
            owners=sorted({campaign_node(month,a.campaign_id) for a in attribution.allocations}-{None})
            nodes.update(owners)
            records.append(dict(record_id=month+':'+str(index),source_artifact=key,
                source_artifact_sha256=digest(text.encode()),record_index=index,
                campaign_nodes=[list(x) for x in owners],unassigned=attribution.has_unassigned_campaign_allocation,
                quantity=str(record.quantity),proceeds=str(record.proceeds),cost_basis=str(record.cost_basis),
                disallowed_loss=str(record.disallowed_loss),gain_loss=str(record.gain_loss),
                allocation_count=len(attribution.allocations)))
    return dict(format='campaigniq.period_scoped_continuity_links',version=1,
        scope='brokerage_equity_and_options',
        source_boundary_plan_sha256=digest(canonical(boundary_plan)),groups=groups,
        historical_candidate_sha256={k:digest(v.encode()) for k,v in historical.items()},
        campaign_nodes=[list(x) for x in sorted(nodes)],
        links=[dict(source=list(a),target=list(b)) for a,b in sorted(edges)],
        realized_records=records,global_campaign_aliases={},
        campaign_identity_merges_performed=False,basis_allocation_inferred=False,
        full_portfolio_continuity_verified=False,publication_authorized=False)


def linked_campaign_nodes(graph,seeds):
    """Return a connected history for review; no identities are renamed."""
    known={tuple(x) for x in graph['campaign_nodes']}
    seeds={tuple(x) for x in seeds}
    if not seeds<=known:raise ValueError('Unknown period-scoped campaign node.')
    adjacency=defaultdict(set)
    for edge in graph['links']:
        a,b=tuple(edge['source']),tuple(edge['target'])
        adjacency[a].add(b);adjacency[b].add(a)
    visited=set(seeds);pending=list(seeds)
    while pending:
        for neighbor in adjacency[pending.pop()]:
            if neighbor not in visited:visited.add(neighbor);pending.append(neighbor)
    return sorted(visited&known)


def summarize_records(graph,selected_nodes=None):
    """Count broker economics once, never once per campaign link or fill.

    A record with owners outside a selected history is explicitly shared and
    excluded from that history's certified total. Unassigned records are not
    silently attached to an unrelated campaign. Full-portfolio totals retain
    all such source records once.
    """
    selected=None if selected_nodes is None else {tuple(x) for x in selected_nodes}
    known={tuple(x) for x in graph['campaign_nodes']}
    if selected is not None and not selected<=known:raise ValueError('Unknown campaign selection.')
    seen=set();included=[];shared=[]
    totals={k:Decimal(0) for k in ('proceeds','cost_basis','disallowed_loss','gain_loss')}
    for row in graph['realized_records']:
        rid=row['record_id']
        if rid in seen:raise ValueError('Duplicate realized record identity.')
        seen.add(rid);owners={tuple(x) for x in row['campaign_nodes']}
        if selected is not None:
            if not owners&selected:continue
            if row['unassigned'] or not owners<=selected:
                shared.append(rid);continue
        for name in totals:totals[name]+=Decimal(row[name])
        included.append(rid)
    if totals['proceeds']-totals['cost_basis']+totals['disallowed_loss']!=totals['gain_loss']:
        raise ValueError('Selected broker totals fail monetary conservation.')
    return dict(record_ids=included,record_count=len(included),shared_record_ids=shared,
        totals={k:str(v) for k,v in totals.items()},
        selected_history_monetary_coverage_complete=not shared,
        identity_aliases_applied=False,publication_authorized=False)


def verify_partition(graph):
    remaining={tuple(x) for x in graph['campaign_nodes']};histories=[];seen=set()
    for row in graph['realized_records']:
        if row['unassigned']:raise ValueError('An unassigned realized record prevents a complete history partition.')
    while remaining:
        nodes=linked_campaign_nodes(graph,[min(remaining)])
        remaining-=set(nodes)
        summary=summarize_records(graph,nodes)
        if summary['shared_record_ids']:raise ValueError('A realized record spans separate linked histories.')
        if seen.intersection(summary['record_ids']):raise ValueError('A realized record was counted in multiple histories.')
        seen.update(summary['record_ids']);histories.append(dict(campaign_nodes=[list(x) for x in nodes],**summary))
    portfolio=summarize_records(graph)
    if seen!=set(portfolio['record_ids']):raise ValueError('Some realized records are missing from the linked histories.')
    for name,total in portfolio['totals'].items():
        if sum((Decimal(row['totals'][name]) for row in histories),Decimal(0))!=Decimal(total):
            raise ValueError('History totals differ from the original broker record totals.')
    return dict(scope=graph['scope'],linked_history_count=len(histories),realized_record_count=portfolio['record_count'],
        exact_once_record_partition_verified=True,broker_monetary_totals_conserved=True,
        histories=histories,totals=portfolio['totals'],
        full_portfolio_continuity_verified=False,publication_authorized=False)


def main():
    import argparse,gzip,os,subprocess,uuid
    from pathlib import Path
    from datetime import datetime,timezone
    from campaigniq.historical_backfill_restored_publisher import load_inputs,save
    from campaigniq.historical_backfill_bundle import decode
    from campaigniq.historical_backfill_correction_rehearsal import verified_preview
    from campaigniq.importers.schwab.realized_gain_loss_reader import read_realized_gain_loss_section
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repo','package','capture','correction-preview','correction-rehearsal-report','april-realized-report'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--development-only',required=True,action='store_true');args=p.parse_args()
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):p.error('Use local development only.')
    repo=args.repo.expanduser().resolve()
    if Path(__file__).resolve().parents[2]!=repo:p.error('Use the installed development module.')
    def git(*parts):return subprocess.check_output(['git','-C',str(repo),*parts],text=True).strip()
    if git('branch','--show-current')!='feature/historical-backfill-2025' or git('status','--porcelain'):
        p.error('Use the clean, committed development checkout.')
    package,bundle,candidates=load_inputs(args.package.expanduser(),args.capture.expanduser())
    path=args.correction_preview.expanduser();preview=decode(path.read_bytes())
    rehearsal=decode(args.correction_rehearsal_report.expanduser().read_bytes())
    if (rehearsal.get('format')!='campaigniq.correction_rehearsal'
        or rehearsal.get('capture_sha256')!=package['capture_sha256']
        or preview.get('capture_sha256')!=package['capture_sha256']
        or rehearsal.get('preview_file_sha256')!=digest(path.read_bytes())
        or rehearsal.get('proposed_artifact_sha256')!=preview['proposed_artifact_sha256']
        or rehearsal.get('production_connection_made') is not False
        or rehearsal.get('publication_authorized') is not False):p.error('Correction rehearsal evidence differs.')
    for flag in ('committed_corrections_verified','correction_transaction_rollback_verified',
        'original_artifact_and_source_archive_verified','exact_all_user_table_rollback_verified'):
        if rehearsal.get(flag) is not True:p.error('A successful correction rehearsal is required.')
    pdf=args.april_realized_report.expanduser();source=pdf.read_bytes()
    if digest(source)!=preview['april_source_pdf_sha256']:p.error('Reviewed PDF checksum differs.')
    text=subprocess.run(['pdftotext','-layout',str(pdf),'-'],check=True,capture_output=True,text=True,timeout=60).stdout
    records=read_realized_gain_loss_section(text.splitlines())
    proposals={k:path.with_name(k).read_text() for k in preview['proposed_artifact_sha256']}
    corrections=verified_preview(bundle['artifacts'],records,preview,proposals,source)
    graph=build_links(package['boundary_plan'],dict(bundle['artifacts'],**candidates,**corrections))
    result=verify_partition(graph)
    result.update(format='campaigniq.linked_history_accounting_review',version=1,development_commit=git('rev-parse','HEAD'),
        capture_sha256=package['capture_sha256'],package_sha256=digest((args.package.expanduser()/'publication-package.json').read_bytes()),
        correction_rehearsal_file_sha256=digest(args.correction_rehearsal_report.expanduser().read_bytes()),
        continuity_graph_sha256=digest(canonical(graph)),boundary_instrument_group_count=len(graph['groups']),
        no_database_connection_made=True,source_identity_replacements_performed=False)
    output=Path.home()/'Downloads'/('CampaignIQ-continuity-links-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    output.mkdir(mode=0o700,exist_ok=False)
    save(output/'continuity-links.json',graph);save(output/'linked-history-review.json',result)
    print('Boundary instrument groups:',len(graph['groups']))
    print('Linked brokerage histories:',result['linked_history_count'])
    print('Realized records counted exactly once:',result['realized_record_count'])
    print('Broker monetary totals conserved: PASS')
    print('Report:',output/'linked-history-review.json')
    print('Offline review only. No records changed; full portfolio continuity and publication remain unverified.')


if __name__=='__main__':main()
