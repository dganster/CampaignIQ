import copy
import json
import pytest
from campaigniq.historical_backfill_continuity_links import (
    build_links,linked_campaign_nodes,summarize_records,verify_partition,
)


def row(rid,owners,*,unassigned=False):
    return dict(record_id=rid,campaign_nodes=owners,unassigned=unassigned,
        proceeds='100.00',cost_basis='80.00',disallowed_loss='0',gain_loss='20.00')


def graph():
    return dict(scope='brokerage_equity_and_options',campaign_nodes=[['2025-12','A'],['2026-01','B']],
        links=[dict(source=['2025-12','A'],target=['boundary_group','G']),
               dict(source=['boundary_group','G'],target=['2026-01','B'])],
        realized_records=[row('2025-12:0',[['2025-12','A']]),row('2026-01:0',[['2026-01','B']])])


def test_repeated_link_paths_do_not_duplicate_records_or_rename_campaigns():
    g=graph();g['links']*=3;before=copy.deepcopy(g)
    nodes=linked_campaign_nodes(g,[['2025-12','A']])
    assert nodes==[('2025-12','A'),('2026-01','B')]
    summary=summarize_records(g,nodes)
    assert summary['record_count']==2 and summary['totals']['gain_loss']=='40.00'
    result=verify_partition(g)
    assert result['linked_history_count']==1 and result['realized_record_count']==2
    assert result['full_portfolio_continuity_verified'] is False
    assert g==before


def test_partial_selection_flags_shared_records_instead_of_assigning_all_profit():
    g=graph();g['realized_records']=[row('shared',[['2025-12','A'],['2026-01','B']])]
    result=summarize_records(g,[['2025-12','A']])
    assert result['shared_record_ids']==['shared'] and result['record_count']==0
    assert result['selected_history_monetary_coverage_complete'] is False
    assert summarize_records(g)['record_count']==1


def test_shared_record_between_disconnected_histories_blocks_partition():
    g=graph();g['links']=[];g['realized_records']=[row('shared',[['2025-12','A'],['2026-01','B']])]
    with pytest.raises(ValueError,match='separate'):verify_partition(g)


def test_duplicate_record_identity_is_rejected():
    g=graph();g['realized_records'].append(copy.deepcopy(g['realized_records'][0]))
    with pytest.raises(ValueError,match='Duplicate'):summarize_records(g)


def test_unassigned_economics_are_retained_in_portfolio_but_block_certified_partition():
    g=graph();g['realized_records'].append(row('unassigned',[],unassigned=True))
    assert summarize_records(g)['record_count']==3
    with pytest.raises(ValueError,match='unassigned'):verify_partition(g)


def test_identical_labels_in_unlinked_periods_are_not_global_aliases():
    g=graph();g['campaign_nodes'].append(['2026-02','A'])
    assert ('2026-02','A') not in linked_campaign_nodes(g,[['2025-12','A']])


def test_unknown_selection_cannot_silently_return_zero():
    with pytest.raises(ValueError):linked_campaign_nodes(graph(),[['2026-01','UNKNOWN']])
    with pytest.raises(ValueError):summarize_records(graph(),[['2026-01','UNKNOWN']])


@pytest.fixture
def boundary():
    ins=dict(type='instrument',symbol='ORCL');key=json.dumps(ins,sort_keys=True,separators=(',',':'))
    def lot(lid,quantity,cid):return dict(lot_id=lid,quantity=quantity,campaign_id=cid,instrument=key,opened_at='2025-12-31T23:59:59.999999')
    return dict(existing_artifact_sha256={},boundaries=[dict(month='2026-05',review_groups=[dict(
        instrument=ins,aggregate_quantity_match=True,classification='single_origin_split_candidate',
        prior_lots=[lot('ORIGINAL','500','A')],published_opening_lots=[lot('ORIGINAL','400','B'),lot('CHILD','100','A')])])])


def test_split_preserves_parent_child_identities_without_inventing_basis(boundary):
    before=copy.deepcopy(boundary);g=build_links(boundary,{})
    assert g['global_campaign_aliases']=={} and g['basis_allocation_inferred'] is False
    assert g['groups'][0]['prior_lots'][0]['lot_id']=='ORIGINAL'
    assert [x['lot_id'] for x in g['groups'][0]['published_opening_lots']]==['ORIGINAL','CHILD']
    assert len(g['campaign_nodes'])==3
    assert boundary==before


def test_exact_lot_groups_do_not_connect_independent_campaigns(boundary):
    g=boundary['boundaries'][0]['review_groups'][0];g['classification']='exact_lots'
    g['prior_lots']=[dict(g['prior_lots'][0],lot_id='ONE',quantity='300',campaign_id='A'),
        dict(g['prior_lots'][0],lot_id='TWO',quantity='200',campaign_id='B')]
    g['published_opening_lots']=copy.deepcopy(g['prior_lots'])
    links=build_links(boundary,{})
    assert linked_campaign_nodes(links,[['2026-04','A']])==[('2026-04','A'),('2026-05','A')]
    assert ('2026-05','B') not in linked_campaign_nodes(links,[['2026-04','A']])


@pytest.mark.parametrize('change',['quantity','instrument','origin_date','duplicate','multiple_parents','unsupported'])
def test_unsupported_or_changed_boundary_evidence_is_refused(boundary,change):
    g=boundary['boundaries'][0]['review_groups'][0]
    if change=='quantity':g['published_opening_lots'][0]['quantity']='399'
    if change=='instrument':g['published_opening_lots'][0]['instrument']='different'
    if change=='origin_date':g['published_opening_lots'][0]['opened_at']='2026-01-01T00:00:00'
    if change=='duplicate':g['published_opening_lots'][1]['lot_id']='ORIGINAL'
    if change=='multiple_parents':
        g['prior_lots'][0]['quantity']='250';g['prior_lots'].append(dict(g['prior_lots'][0],lot_id='SECOND'))
    if change=='unsupported':g['classification']='ambiguous_multiple_origin_regrouping'
    with pytest.raises(ValueError):build_links(boundary,{})
