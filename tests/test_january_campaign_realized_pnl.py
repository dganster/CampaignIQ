from datetime import date, datetime
from decimal import Decimal as D

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.campaign_realized_pnl import (
    aggregate_campaign_realized_pnl,
)
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.thinkorswim.trade_history_reader import (
    ThinkorswimTradeHistoryReader,
)
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader

from tests.test_january_realized_lot_attribution import (
    january_records,
    seed,
    seed_option,
)


DATA_FILE = "tests/data/thinkorswim/Account Trading History 2026.csv"


def _january_trades():
    statement = ThinkorswimSourceReader().read(DATA_FILE)
    orders = ThinkorswimTradeHistoryReader().read(
        statement.section("Account Trade History")
    )

    trades = []

    for order in orders:
        if any(row.option_type.upper() == "FOREX" for row in order.legs):
            continue

        trade = to_trade(order)

        if min(
            ex.executed_at
            for leg in trade.legs
            for ex in leg.executions
        ).date() <= date(2026, 1, 31):
            trades.append(trade)

    return trades


def _seed_january_boundary_book() -> LotBook:
    book = LotBook()

    for row in [
        ("COIN", "500", "128703.31"),
        ("COST", "500", "452378.31"),
        ("HD", "500", "175299.07"),
        ("LIN", "500", "213921.31"),
        ("NFLX", "5000", "534533.19"),
        ("ORCL", "500", "101558.31"),
        ("TMUS", "500", "105494.31"),
        ("VRT", "500", "93810.00"),
    ]:
        seed(book, *row)

    for row in [
        ("COIN", "2026-01-16", "220", "C", "-5", "-14221.69"),
        ("COST", "2026-01-16", "840", "C", "-5", "-22336.69"),
        ("HD", "2026-01-16", "335", "C", "-5", "-11996.69"),
        ("LIN", "2026-02-20", "365", "C", "-5", "-32046.69"),
        ("NFLX", "2026-02-20", "86", "C", "-50", "-52316.81"),
        ("ORCL", "2026-02-20", "175", "C", "-5", "-13511.69"),
        ("TMUS", "2026-02-20", "175", "C", "-5", "-13441.69"),
        ("VRT", "2026-02-20", "145", "C", "-5", "-14356.69"),
        ("AMZN", "2026-01-16", "240", "P", "-5", "-7071.69"),
        ("CRWD", "2026-01-16", "500", "P", "-5", "-12441.69"),
        ("LLY", "2026-01-16", "1040", "P", "-5", "-30881.69"),
        ("LMT", "2026-01-16", "480", "P", "-5", "-15696.69"),
        ("META", "2026-01-16", "690", "P", "-5", "-30331.69"),
        ("MSFT", "2026-01-16", "495", "P", "-5", "-9031.69"),
        ("NVDA", "2026-01-16", "184", "P", "-5", "-5391.69"),
    ]:
        seed_option(book, *row)

    return book


def test_actual_january_campaign_realized_pnl() -> None:
    trades = _january_trades()
    campaigns = CampaignReconstructor().reconstruct(trades)

    book = _seed_january_boundary_book()

    for campaign in campaigns:
        book.resolve_boundary_campaign(campaign)

    events = [
        PositionEvent(
            kind=PositionEventKind.ASSIGNMENT,
            changes=(PositionChange(Instrument("DXCM"), D("-500")),),
            occurred_at=datetime(2026, 1, 9, 16),
        ),
        PositionEvent(
            kind=PositionEventKind.ASSIGNMENT,
            changes=(PositionChange(Instrument("EL"), D("-500")),),
            occurred_at=datetime(2026, 1, 9, 16),
        ),
    ]

    records = january_records()

    attributions = RealizedLotAttributor(book).attribute_campaigns(
        campaigns,
        records,
        events,
    )

    results = aggregate_campaign_realized_pnl(attributions)

    assert len(attributions) == 28

    broker_gain_loss = sum(
        (record.gain_loss for record in records),
        D("0"),
    )

    attributed_gain_loss = sum(
        (result.gain_loss for result in results),
        D("0"),
    )

    assert broker_gain_loss == D("126642.32")
    assert attributed_gain_loss == D("97954.75")

    unassigned_gain_loss = sum(
        (
            attribution.record.gain_loss
            for attribution in attributions
            if attribution.has_unassigned_campaign_allocation
        ),
        D("0"),
    )

    assert unassigned_gain_loss == D("28687.57")

    unassigned_records = {
        (
            attribution.record.closed_date,
            str(attribution.record.instrument),
            attribution.record.gain_loss,
        )
        for attribution in attributions
        if attribution.has_unassigned_campaign_allocation
    }

    assert unassigned_records == {
        (
            date(2026, 1, 22),
            "OptionContract(underlying='ORCL', expiration=datetime.date(2026, 2, 20), strike=Decimal('175'), option_type=<OptionType.CALL: 'CALL'>)",
            D("9079.39"),
        ),
        (
            date(2026, 1, 23),
            "Instrument(symbol='COIN')",
            D("-19983.41"),
        ),
        (
            date(2026, 1, 23),
            "Instrument(symbol='COST')",
            D("39591.59"),
        ),
    }
    assert broker_gain_loss - attributed_gain_loss == D("28687.57")

    camp_000001 = next(
        result
        for result in results
        if result.campaign_id == "CAMP-000001"
    )

    assert camp_000001.gain_loss == D("11640.16")
    assert camp_000001.fully_reconciled is True
