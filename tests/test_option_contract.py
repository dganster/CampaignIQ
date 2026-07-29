from datetime import date
from decimal import Decimal

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType


def test_option_contract_fields() -> None:
    contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 2, 20),
        strike=Decimal("220"),
        option_type=OptionType.CALL,
    )

    assert contract.underlying == "IBM"
    assert contract.expiration == date(2026, 2, 20)
    assert contract.strike == Decimal("220")
    assert contract.option_type is OptionType.CALL
