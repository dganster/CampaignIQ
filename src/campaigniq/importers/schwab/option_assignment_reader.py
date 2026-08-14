"""Read Schwab option-assignment statement rows."""

from datetime import datetime

from campaigniq.importers.schwab.option_assignment import (
    SchwabOptionAssignment,
)
from campaigniq.importers.schwab.parsers import (
    parse_date,
    parse_decimal,
)


def read_option_assignment(
    *,
    date_value: str,
    symbol: str,
    contract: str,
    quantity: str,
) -> SchwabOptionAssignment:
    """Convert one Schwab Option Assignment row to a domain-ready object."""

    parts = contract.split()

    if len(parts) != 3:
        raise ValueError(
            f"Unsupported option contract: {contract!r}"
        )

    occurred_at = datetime.combine(
        parse_date(date_value),
        datetime.min.time(),
    )

    expiration = parse_date(parts[0])
    strike = parse_decimal(parts[1])
    option_code = parts[2].upper()

    if option_code not in {"C", "P"}:
        raise ValueError(
            f"Unsupported option type: {option_code!r}"
        )

    option_type = "CALL" if option_code == "C" else "PUT"

    return SchwabOptionAssignment(
        occurred_at=occurred_at,
        symbol=symbol.strip(),
        expiration=expiration,
        strike=strike,
        option_type=option_type,
        quantity=parse_decimal(quantity),
    )
