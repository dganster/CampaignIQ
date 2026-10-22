from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.importers.schwab.position_snapshot import SchwabPositionSnapshotRow
from campaigniq.pending_opening_inventory import reconstruct_pending_opening_inventory


def test_spot_january_rollover():
    jan_end = date(2025, 1, 31)
    old = OptionContract("SPOT", date(2025, 2, 21), Decimal("480"), OptionType.PUT)
    new = OptionContract("SPOT", date(2025, 3, 21), Decimal("500"), OptionType.PUT)

    snapshot = (
        SchwabPositionSnapshotRow(
            symbol="SPOT",
            quantity=Decimal("-5"),
            snapshot_at=datetime(2025, 1, 31, 23, 59, 59),
            expiration=old.expiration,
            strike=old.strike,
            option_type=old.option_type,
            basis_total=Decimal("-4440.05"),
        ),
    )

    lines = [
        "Pending / Open Activity",
        "01/31 Cover Short SPOT PUT SPOTIFY TECHNOLOGY S 5.0000 7.9400 02/03",
        "02/21/2025 480.00 P",
        "01/31 Short Sale SPOT PUT SPOTIFY TECHNOLOGY S 5.0000 17.0900 02/03",
        "03/21/2025 500.00 P",
        "Total Pending Transactions",
        "Endnotes For Your Account",
    ]

    book = reconstruct_pending_opening_inventory(
        snapshot, lines, period_end=jan_end
    )

    quantities = {
        instrument: sum((lot.quantity for lot in lots), Decimal("0"))
        for instrument, lots in book._lots.items()
    }

    assert quantities.get(old, Decimal("0")) == 0
    assert quantities[new] == Decimal("-5")
    assert book._lots[new][0].basis_total is None


def test_spot_unmatched_pending_close_rejected():
    lines = [
        "Pending / Open Activity",
        "01/31 Cover Short SPOT PUT SPOTIFY TECHNOLOGY S 5.0000 7.9400 02/03",
        "02/21/2025 480.00 P",
        "Total Pending Transactions",
        "Endnotes For Your Account",
    ]

    with pytest.raises(ValueError, match="Pending close exceeds settled inventory"):
        reconstruct_pending_opening_inventory(
            (
                SchwabPositionSnapshotRow(
                    symbol="SPOT",
                    quantity=Decimal("-4"),
                    snapshot_at=datetime(2025, 1, 31, 23, 59, 59),
                    expiration=date(2025, 2, 21),
                    strike=Decimal("480"),
                    option_type=OptionType.PUT,
                ),
            ),
            lines,
            period_end=date(2025, 1, 31),
        )
