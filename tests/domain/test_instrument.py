from datetime import date
from decimal import Decimal

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument


def test_stock_instrument_has_itself_as_underlying() -> None:
    instrument = Instrument("SPX")

    assert instrument.underlying == "SPX"


def test_option_contract_has_its_underlying() -> None:
    contract = OptionContract(
        underlying="SPX",
        expiration=date(2026, 3, 20),
        strike=Decimal("5650"),
        option_type=OptionType.CALL,
    )

    assert contract.underlying == "SPX"
