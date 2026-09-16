from datetime import date
from decimal import Decimal
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.ui.dashboard_campaigns import aggregate_period_qualified_campaigns

def attr(cid, symbol, day, pnl):
    gain = Decimal(pnl); basis = Decimal("100")
    return RealizedAttribution(
        record=RealizedGainLossRecord(
            closed_date=day, instrument=Instrument(symbol), quantity=Decimal("1"),
            closing_price=Decimal("1"), proceeds=basis+gain, cost_basis=basis,
            gain_loss=gain, basis_method="FIFO", term="SHORT",
        ),
        allocations=(LotAllocation(
            lot_id=f"{symbol}-{day}", quantity=Decimal("1"), broker_basis=basis,
            basis_source="TEST", campaign_id=cid,
        ),),
    )

def test_same_local_id_in_two_periods_stays_two_campaigns():
    monthly = {
        (date(2026,1,1), date(2026,1,31)): (attr("CAMP-000001","AAPL",date(2026,1,10),"100"),),
        (date(2026,2,1), date(2026,2,28)): (attr("CAMP-000001","MSFT",date(2026,2,10),"-40"),),
    }
    results, drilldowns = aggregate_period_qualified_campaigns(monthly)
    assert {r.campaign_id for r in results} == {"2026-01/CAMP-000001","2026-02/CAMP-000001"}
    assert {d.symbols for d in drilldowns} == {("AAPL",),("MSFT",)}

def test_forex_campaign_ids_are_period_qualified():
    from types import SimpleNamespace
    from campaigniq.domain.forex_settlement_attribution import ForexSettlementAttribution
    period = (date(2026, 1, 1), date(2026, 1, 31))
    forex = {period: (ForexSettlementAttribution(
        settlement=SimpleNamespace(settlement_pl_usd=Decimal("25.50")),
        campaign_id="CAMP-000001",
    ),)}
    results, drilldowns = aggregate_period_qualified_campaigns({}, forex)
    assert len(results) == 1
    assert results[0].campaign_id == "2026-01/CAMP-000001"
    assert results[0].gain_loss == Decimal("25.50")
    assert results[0].record_count == 1
    assert results[0].allocation_count == 0
    assert results[0].fully_reconciled
    assert drilldowns == ()

def test_same_forex_local_id_in_two_periods_stays_two_campaigns():
    from types import SimpleNamespace
    from campaigniq.domain.forex_settlement_attribution import ForexSettlementAttribution
    def fx_attr(pnl):
        return ForexSettlementAttribution(
            settlement=SimpleNamespace(settlement_pl_usd=Decimal(pnl)),
            campaign_id="CAMP-000001",
        )
    forex = {
        (date(2026, 1, 1), date(2026, 1, 31)): (fx_attr("10"),),
        (date(2026, 2, 1), date(2026, 2, 28)): (fx_attr("-5"),),
    }
    results, _ = aggregate_period_qualified_campaigns({}, forex)
    assert {result.campaign_id for result in results} == {
        "2026-01/CAMP-000001",
        "2026-02/CAMP-000001",
    }
