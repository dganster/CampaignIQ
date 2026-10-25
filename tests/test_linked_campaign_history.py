from copy import deepcopy
from datetime import date
from decimal import Decimal as D
import json

import pytest

from campaigniq.analytics.linked_campaign_history import LinkedCampaignHistory, load_linked_history, CONTINUITY_KEY
from campaigniq.historical_backfill_continuity_links import build_links
from campaigniq.historical_backfill_publication_package import canonical, digest
from campaigniq.persistence.realized_attribution_store import serialize_realized_attributions
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.value_objects.instrument import Instrument


class Storage:
    def __init__(self, artifacts): self.artifacts=artifacts
    def exists(self, key): return key in self.artifacts
    def read_text(self, key): return self.artifacts[key]
    def list_keys(self, *, prefix='', suffix=''):
        return tuple(k for k in self.artifacts if k.startswith(prefix) and k.endswith(suffix))


@pytest.fixture
def source():
    artifacts={}
    for month,cid in ((1,'OLD'),(2,'NEW')):
        start=date(2026,month,1);end=date(2026,month,31 if month==1 else 28)
        record=RealizedGainLossRecord(end,Instrument('ABC'),D(1),D(100),D(100),D(80),D(20),'FIFO','ST',D(0))
        allocation=LotAllocation('L',D(1),D(80),campaign_id=cid)
        artifacts[f'2026-{month:02}-realized-attributions.json']=serialize_realized_attributions(
            period_start=start,period_end=end,attributions=[RealizedAttribution(record,(allocation,))])
    ins=dict(type='instrument',symbol='ABC');inskey=canonical(ins).decode()
    def lot(cid):return dict(lot_id='L',quantity='1',campaign_id=cid,instrument=inskey,opened_at='2025-12-01T12:00:00')
    plan=dict(existing_artifact_sha256={k:digest(v.encode()) for k,v in artifacts.items()},
        boundaries=[dict(month='2026-02',review_groups=[dict(instrument=ins,aggregate_quantity_match=True,
            classification='same_lots_campaign_labels',prior_lots=[lot('OLD')],published_opening_lots=[lot('NEW')])])])
    graph=build_links(plan,artifacts)
    archive=dict(format='campaigniq.combined_rehearsal_archive',continuity_graph_sha256=digest(canonical(graph)),boundary_plan=plan)
    stored=dict(artifacts,**{CONTINUITY_KEY:canonical(graph).decode(),'combined-rehearsal-test.json':canonical(archive).decode()})
    return graph,artifacts,plan,stored


def test_changed_ids_have_exact_once_combined_broker_results(source):
    graph,artifacts,plan,stored=source
    original=deepcopy(source)
    reader=load_linked_history(Storage(stored));selection=reader.selection('2026-02/NEW')
    assert selection['record_count']==2
    assert selection['totals']['gain_loss']=='40'
    assert [r.campaign_ids for r in selection['records']]==[('2026-01/OLD',),('2026-02/NEW',)]
    assert source==original


def test_missing_continuity_preserves_existing_behavior():
    assert load_linked_history(Storage({})) is None


def test_unrelated_id_is_not_inferred_by_symbol(source):
    graph,artifacts,plan,_=source
    reader=LinkedCampaignHistory(graph,artifacts,plan)
    assert reader.selection('2026-02/OTHER') is None


def test_history_rows_are_qualified_by_period_and_campaign(source):
    reader=load_linked_history(Storage(source[3]));selection=reader.selection('2026-02/NEW')
    entries=[dict(period_start='2026-01-01',campaign_id='OLD'),dict(period_start='2026-02-01',campaign_id='NEW'),
        dict(period_start='2026-03-01',campaign_id='OLD'),dict(period_start='2026-02-01',campaign_id='OTHER')]
    assert reader.entries(entries,selection)==entries[:2]


@pytest.mark.parametrize('change',['amount','index','link','plan','missing_archive','missing_source'])
def test_changed_or_missing_evidence_is_rejected(source,change):
    graph,artifacts,plan,stored=source
    if change in ('amount','index','link'):
        if change=='amount':graph['realized_records'][0]['gain_loss']='200'
        if change=='index':graph['realized_records'][0]['record_index']=100
        if change=='link':graph['links']=[]
        with pytest.raises(ValueError):LinkedCampaignHistory(graph,artifacts,plan)
    else:
        if change=='plan':
            archive=json.loads(stored['combined-rehearsal-test.json']);archive['boundary_plan']['boundaries']=[]
            stored['combined-rehearsal-test.json']=json.dumps(archive)
        if change=='missing_archive':del stored['combined-rehearsal-test.json']
        if change=='missing_source':del stored['2026-01-realized-attributions.json']
        with pytest.raises((ValueError,KeyError)):load_linked_history(Storage(stored))


def test_source_record_change_invalidates_graph(source):
    graph,artifacts,plan,_=source
    key='2026-01-realized-attributions.json';obj=json.loads(artifacts[key])
    obj['attributions'][0]['record']['gain_loss']='21';artifacts[key]=json.dumps(obj)
    with pytest.raises(ValueError):LinkedCampaignHistory(graph,artifacts,plan)
