"""Translate Thinkorswim objects into CampaignIQ domain objects."""

from campaigniq.domain.option_contract import OptionContract
from campaigniq.importers.thinkorswim.parsers import parse_option_type
from campaigniq.importers.thinkorswim.trade_row import ThinkorswimTradeRow


def to_option_contract(row: ThinkorswimTradeRow) -> OptionContract:
    """Translate a Thinkorswim trade row into a domain OptionContract."""

    if row.option_type.upper() not in {"CALL", "PUT"}:
        raise ValueError(
            f"Trade row is not an option trade: {row.option_type!r}"
        )

    if row.exp is None:
        raise ValueError("Option trade is missing an expiration date.")

    if row.strike is None:
        raise ValueError("Option trade is missing a strike price.")

    return OptionContract(
        underlying=row.symbol,
        expiration=row.exp,
        strike=row.strike,
        option_type=parse_option_type(row.option_type),
    )
