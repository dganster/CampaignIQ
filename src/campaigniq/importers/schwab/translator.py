"""Translate Schwab objects into CampaignIQ domain objects."""

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.importers.schwab.option_assignment import (
    SchwabOptionAssignment,
)


def to_position_event(
    assignment: SchwabOptionAssignment,
) -> PositionEvent:
    """Translate a Schwab option assignment into a domain event."""

    option_type = assignment.option_type.strip().upper()

    if option_type == "CALL":
        parsed_option_type = OptionType.CALL
    elif option_type == "PUT":
        parsed_option_type = OptionType.PUT
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
        ),
        occurred_at=assignment.occurred_at,
    )
