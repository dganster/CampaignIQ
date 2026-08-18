"""Translate Schwab objects into CampaignIQ domain objects."""

from decimal import Decimal

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.option_assignment import (
    SchwabOptionAssignment,
)


def to_position_event(
    assignment: SchwabOptionAssignment,
) -> PositionEvent:
    """Translate a Schwab option assignment into a domain event.

    A short-call assignment delivers shares away from the account, while a
    short-put assignment delivers shares into the account.  The assignment
    therefore changes both the option position and the resulting stock
    position.
    """

    option_type = assignment.option_type.strip().upper()

    if option_type == "CALL":
        parsed_option_type = OptionType.CALL
        stock_quantity = -assignment.quantity * Decimal("100")
    elif option_type == "PUT":
        parsed_option_type = OptionType.PUT
        stock_quantity = assignment.quantity * Decimal("100")
    else:
        raise ValueError(
            f"Unsupported Schwab option type: "
            f"{assignment.option_type!r}"
        )

    contract = OptionContract(
        underlying=assignment.symbol,
        expiration=assignment.expiration,
        strike=assignment.strike,
        option_type=parsed_option_type,
    )

    return PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=contract,
                quantity=assignment.quantity,
            ),
            PositionChange(
                instrument=Instrument(assignment.symbol),
                quantity=stock_quantity,
            ),
        ),
        occurred_at=assignment.occurred_at,
    )
