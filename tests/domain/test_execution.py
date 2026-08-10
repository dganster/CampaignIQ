from datetime import datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution


def test_execution_contains_fill_facts() -> None:
    execution = Execution(
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
        executed_at=datetime(2026, 7, 29, 10, 30),
    )

    assert execution.quantity == Decimal("1")
    assert execution.execution_price == Decimal("3.25")
    assert execution.executed_at == datetime(2026, 7, 29, 10, 30)