from decimal import Decimal
from pathlib import Path

from campaigniq.importers.schwab.realized_gain_loss_reader import (
    read_realized_gain_loss_section,
)
from campaigniq.domain.option_contract import OptionContract


DATA = Path("tests/data/schwab/january_realized_gain_loss.txt")


def test_reads_actual_january_realized_gain_loss_report() -> None:
    records = read_realized_gain_loss_section(DATA.read_text().splitlines())

    assert len(records) == 28
    assert sum((record.gain_loss for record in records), Decimal("0")) == Decimal(
        "126642.32"
    )
    assert records[0].closed_date.isoformat() == "2026-01-06"
    assert isinstance(records[0].instrument, OptionContract)
    assert records[0].quantity == Decimal("5")


def test_preserves_february_settlement_row_in_january_report() -> None:
    records = read_realized_gain_loss_section(DATA.read_text().splitlines())

    lin = records[-1]
    assert str(lin.instrument) == (
        "OptionContract(underlying='LIN', expiration=datetime.date(2026, 2, 20), "
        "strike=Decimal('365.00'), option_type=<OptionType.CALL: 'CALL'>)"
    )
    assert lin.closed_date.isoformat() == "2026-02-02"
    assert lin.gain_loss == Decimal("-12681.61")
