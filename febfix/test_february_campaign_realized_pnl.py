from datetime import date, datetime
from decimal import Decimal as D

from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.option_assignment_flow import (
    read_option_assignment_events,
)
from campaigniq.import_pipeline import PeriodImportPipeline


def test_tmus_assignment_matches_february_realized_stock_sale() -> None:
    result = PeriodImportPipeline().run(
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
        thinkorswim_trade_history=(
            "tests/data/thinkorswim/Account Trading History 2026.csv"
        ),
        opening_snapshot="tests/data/schwab/january_positions.txt",
        opening_snapshot_at=datetime(2026, 1, 31, 23, 59, 59),
        realized_gain_loss_report=(
            "tests/data/schwab/february_realized_gain_loss.txt"
        ),
    )

    events = read_option_assignment_events(
        open("tests/data/schwab/february_assignments.txt")
        .read()
        .splitlines()
    )

    attributions = RealizedLotAttributor(
        result.opening_lot_book
    ).attribute_campaigns(
        result.campaigns,
        result.realized_gain_loss,
        events,
    )

    tmus = [
        attribution
        for attribution in attributions
        if attribution.record.instrument == Instrument("TMUS")
        and attribution.record.closed_date == date(2026, 2, 26)
        and attribution.record.quantity == D("500")
    ]

    assert len(tmus) == 1
    assert tmus[0].record.gain_loss == D("-2977.73")
    assert tmus[0].has_unassigned_campaign_allocation is False
