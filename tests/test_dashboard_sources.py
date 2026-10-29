from calendar import monthrange
from datetime import date
from decimal import Decimal
import pytest

from campaigniq import dashboard_sources as module
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.realized_attribution_store import serialize_realized_attributions
from campaigniq.persistence.forex_settlement_attribution_store import serialize_forex_settlement_attributions
from campaigniq.persistence.monthly_publication import ensure_publication_protocol_in_storage,publish_finalized_month_marker_to_storage
from tests.test_account_local_publication import MemoryStorage


def storage():
    store=MemoryStorage('selected-account')
    for month,gain in ((1,'10.00'),(2,'-3.00'),(3,'99.00')):
        start=date(2025,month,1);end=date(2025,month,monthrange(2025,month)[1])
        record=RealizedGainLossRecord(start,Instrument('SPY'),Decimal(1),Decimal(100),Decimal(100),
            Decimal(100)-Decimal(gain),Decimal(gain),'FIFO','SHORT')
        store.write_text(f'2025-{month:02d}-realized-attributions.json',serialize_realized_attributions(
            period_start=start,period_end=end,attributions=(RealizedAttribution(record,()),)))
    store.write_text('2025-01-forex-settlement-attributions.json',serialize_forex_settlement_attributions(
        period_start=date(2025,1,1),period_end=date(2025,1,31),attributions=()))
    ensure_publication_protocol_in_storage(store,first_period_end=date(2025,2,28))
    publish_finalized_month_marker_to_storage(store,period_end=date(2025,2,28))
    return store


def test_shared_loader_keeps_legacy_visibility_markers_and_exact_broker_totals():
    store=storage();before=dict(store.rows)
    summaries,monthly,forex=module.load_dashboard_sources(store)
    assert [row.period_start for row in summaries]==[date(2025,1,1),date(2025,2,1)]
    assert [row.equity_options_realized_pnl for row in summaries]==[Decimal('10.00'),Decimal('-3.00')]
    assert len(monthly)==2 and set(forex)=={(date(2025,1,1),date(2025,1,31))}
    assert store.rows==before


def test_unparseable_artifact_keys_are_ignored_as_in_existing_dashboard():
    store=storage();store.rows['unrelated-realized-attributions.json']='preserved'
    assert module.published_artifact_keys(store,suffix='-realized-attributions.json')==(
        '2025-01-realized-attributions.json','2025-02-realized-attributions.json')


def test_no_visible_period_raises_without_initializing_or_writing():
    store=MemoryStorage('empty')
    with pytest.raises(FileNotFoundError,match='No persisted attribution'):module.load_dashboard_sources(store)
    assert store.rows=={}


def test_loader_cannot_write_even_when_injected_reader_attempts_it(monkeypatch):
    store=storage();before=dict(store.rows)
    def write(storage,**kwargs):storage.write_text('blocked.json','{}')
    monkeypatch.setattr(module,'load_persisted_monthly_campaign_attributions_from_storage',write)
    with pytest.raises(PermissionError):module.load_dashboard_sources(store)
    assert store.rows==before


def test_duplicate_embedded_periods_keep_existing_validation():
    store=storage()
    store.rows['2025-02-realized-attributions.json']=store.rows['2025-01-realized-attributions.json']
    with pytest.raises(ValueError,match='Duplicate persisted'):module.load_dashboard_sources(store)
