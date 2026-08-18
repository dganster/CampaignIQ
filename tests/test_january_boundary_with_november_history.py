from datetime import date, datetime
from pathlib import Path

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.boundary_reconstruction import BoundaryReconstructionAnalyzer
from campaigniq.domain.lot_book import LotBook
from campaigniq.importers.schwab.position_snapshot import to_lots
from campaigniq.importers.schwab.position_snapshot_reader import (
    read_position_snapshot_section,
)
from campaigniq.importers.thinkorswim.trade_history_reader import (
    ThinkorswimTradeHistoryReader,
)
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


JANUARY_TRADE_HISTORY = "tests/data/thinkorswim/Account Trading History 2026.csv"
DECEMBER_TRADE_HISTORY = "tests/data/thinkorswim/Account Trade History December 2025.csv"
NOVEMBER_TRADE_HISTORY = "tests/data/thinkorswim/Account Trade History November 2025.csv"
DECEMBER_POSITIONS = Path("tests/data/schwab_december_positions.txt")


def _trade_history(filename: str, *, through: date):
    statement = ThinkorswimSourceReader().read(filename)
    orders = ThinkorswimTradeHistoryReader().read(
        statement.section("Account Trade History")
    )

    trades = []
    for order in orders:
        if any(row.option_type.upper() == "FOREX" for row in order.legs):
            continue

        trade = to_trade(order)
        if min(
            execution.executed_at
            for leg in trade.legs
            for execution in leg.executions
        ).date() <= through:
            trades.append(trade)

    return trades


def test_actual_november_history_resolves_all_january_boundary_campaigns() -> None:
    rows = read_position_snapshot_section(
        DECEMBER_POSITIONS.read_text().splitlines(),
        snapshot_at=datetime(2025, 12, 31),
    )

    opening_lot_book = LotBook()
    for lot in to_lots(list(rows)):
        opening_lot_book.seed(lot)

    january_campaigns = CampaignReconstructor().reconstruct(
        _trade_history(JANUARY_TRADE_HISTORY, through=date(2026, 1, 31))
    )

    historical_trades = [
        *_trade_history(NOVEMBER_TRADE_HISTORY, through=date(2025, 11, 30)),
        *_trade_history(DECEMBER_TRADE_HISTORY, through=date(2025, 12, 31)),
    ]

    result = BoundaryReconstructionAnalyzer().analyze(
        period_start=date(2026, 1, 1),
        campaigns=january_campaigns,
        opening_lot_book=opening_lot_book,
        historical_trades=historical_trades,
        historical_period_start=date(2025, 11, 1),
    )

    assert result.unresolved_positions == ()
    assert result.unresolved_campaigns == ()
    assert result.historical_requirements == ()

    resolved = {
        lot.campaign_id
        for instrument in opening_lot_book._lots
        for lot in opening_lot_book.lots(instrument)
        if lot.campaign_id is not None
    }
    assert resolved == {
        "CAMP-000009",  # AMZN
        "CAMP-000012",  # LMT
        "CAMP-000013",
        "CAMP-000014",
        "CAMP-000017",
        "CAMP-000022",
    }
