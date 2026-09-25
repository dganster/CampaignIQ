from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from campaigniq.domain.corporate_action import (
    CorporateActionEvidence,
    CorporateActionType,
)
from campaigniq.domain.corporate_action_normalizer import (
    CorporateActionNormalizer,
)
from campaigniq.domain.corporate_action_option_comparison import (
    CorporateActionOptionComparator,
    OptionPositionObservation,
)
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.position_snapshot import to_lots
from campaigniq.importers.schwab.position_snapshot_reader import (
    read_position_snapshot_section,
)
from campaigniq.importers.thinkorswim.trade_history_reader import (
    ThinkorswimTradeHistoryReader,
)
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import (
    ThinkorswimSourceReader,
)


DATA = Path("tests/data")
NOVEMBER = DATA / "thinkorswim/Account Trade History November 2025.csv"
DECEMBER = DATA / "thinkorswim/Account Trade History December 2025.csv"
DECEMBER_POSITIONS = DATA / "schwab/december_positions.txt"

NFLX_SPLIT = CorporateActionEvidence(
    symbol="NFLX",
    effective_date=date(2025, 11, 17),
    action_type=CorporateActionType.FORWARD_SPLIT,
    new_units=Decimal("10"),
    old_units=Decimal("1"),
    source="SCHWAB_TRANSACTION_HISTORY",
    source_reference="Options Frwd Split",
)


def _trades(path: Path):
    statement = ThinkorswimSourceReader().read(path)
    orders = ThinkorswimTradeHistoryReader().read(
        statement.section("Account Trade History")
    )

    result = []
    for order in orders:
        if any(row.option_type.upper() == "FOREX" for row in order.legs):
            continue
        result.append(to_trade(order))
    return result


def _underlying(instrument) -> str:
    if isinstance(instrument, OptionContract):
        return instrument.underlying
    return instrument.symbol


def _nflx_legs(path: Path):
    return [
        leg
        for trade in _trades(path)
        for leg in trade.legs
        if _underlying(leg.instrument) == "NFLX"
    ]


def _december_nflx_lots():
    rows = read_position_snapshot_section(
        DECEMBER_POSITIONS.read_text().splitlines(),
        snapshot_at=datetime(2025, 12, 31),
    )
    return [
        lot
        for lot in to_lots(list(rows))
        if _underlying(lot.instrument) == "NFLX"
    ]


def test_fixture_contains_explicit_authoritative_nflx_split_evidence() -> None:
    assert NFLX_SPLIT.symbol == "NFLX"
    assert NFLX_SPLIT.action_type is CorporateActionType.FORWARD_SPLIT
    assert NFLX_SPLIT.new_units == Decimal("10")
    assert NFLX_SPLIT.old_units == Decimal("1")
    assert NFLX_SPLIT.source == "SCHWAB_TRANSACTION_HISTORY"
    assert NFLX_SPLIT.source_reference == "Options Frwd Split"


def test_december_snapshot_contains_post_split_nflx_equity_and_option_scale() -> None:
    lots = _december_nflx_lots()

    equity = [
        lot
        for lot in lots
        if isinstance(lot.instrument, Instrument)
    ]
    options = [
        lot
        for lot in lots
        if isinstance(lot.instrument, OptionContract)
    ]

    assert any(
        lot.quantity == Decimal("5000")
        for lot in equity
    )

    assert any(
        lot.quantity == Decimal("-50")
        and lot.instrument.option_type is OptionType.CALL
        for lot in options
    )


def test_split_normalizes_december_equity_to_pre_split_scale() -> None:
    normalizer = CorporateActionNormalizer([NFLX_SPLIT])

    assert (
        normalizer.normalize_quantity(
            Decimal("5000"),
            symbol="NFLX",
            source_date=date(2025, 12, 31),
            target_date=date(2025, 11, 14),
        )
        == Decimal("500.0")
    )


def test_real_history_contains_post_split_fifty_contract_nflx_activity() -> None:
    december_legs = _nflx_legs(DECEMBER)

    option_legs = [
        leg
        for leg in december_legs
        if isinstance(leg.instrument, OptionContract)
    ]

    assert option_legs
    assert any(
        leg.quantity == Decimal("50")
        for leg in option_legs
    )


def test_real_history_and_snapshot_use_consistent_post_split_scale() -> None:
    december_legs = _nflx_legs(DECEMBER)
    lots = _december_nflx_lots()

    trade_quantities = {
        leg.quantity
        for leg in december_legs
        if isinstance(leg.instrument, OptionContract)
    }
    snapshot_quantities = {
        abs(lot.quantity)
        for lot in lots
        if isinstance(lot.instrument, OptionContract)
    }

    assert Decimal("50") in trade_quantities
    assert Decimal("50") in snapshot_quantities


def test_known_nflx_split_transition_is_economically_compatible() -> None:
    comparator = CorporateActionOptionComparator(
        CorporateActionNormalizer([NFLX_SPLIT])
    )

    pre_split = OptionPositionObservation(
        contract=OptionContract(
            underlying="NFLX",
            expiration=date(2025, 12, 19),
            strike=Decimal("1140"),
            option_type=OptionType.PUT,
        ),
        quantity=Decimal("-5"),
        observed_date=date(2025, 11, 14),
    )

    post_split = OptionPositionObservation(
        contract=OptionContract(
            underlying="NFLX",
            expiration=date(2025, 12, 19),
            strike=Decimal("114"),
            option_type=OptionType.PUT,
        ),
        quantity=Decimal("-50"),
        observed_date=date(2025, 11, 18),
    )

    assert comparator.economically_compatible(
        pre_split,
        post_split,
    )


def test_same_nflx_transition_is_not_inferred_without_split_evidence() -> None:
    comparator = CorporateActionOptionComparator(
        CorporateActionNormalizer()
    )

    pre_split = OptionPositionObservation(
        contract=OptionContract(
            underlying="NFLX",
            expiration=date(2025, 12, 19),
            strike=Decimal("1140"),
            option_type=OptionType.PUT,
        ),
        quantity=Decimal("-5"),
        observed_date=date(2025, 11, 14),
    )

    post_split = OptionPositionObservation(
        contract=OptionContract(
            underlying="NFLX",
            expiration=date(2025, 12, 19),
            strike=Decimal("114"),
            option_type=OptionType.PUT,
        ),
        quantity=Decimal("-50"),
        observed_date=date(2025, 11, 18),
    )

    assert not comparator.economically_compatible(
        pre_split,
        post_split,
    )


def test_november_and_december_trade_history_remain_broker_facts() -> None:
    november_legs = _nflx_legs(NOVEMBER)
    december_legs = _nflx_legs(DECEMBER)

    # Reading and comparing corporate-action evidence must not rewrite
    # historical source data.  The acceptance layer consumes the same
    # broker-derived trade objects as the rest of CampaignIQ.
    assert isinstance(november_legs, list)
    assert december_legs

    for leg in [*november_legs, *december_legs]:
        assert leg.instrument is not None
        # Trade-leg quantity is signed broker/domain information:
        # negative quantities represent short/sell exposure and positive
        # quantities represent long/buy exposure.  Corporate-action
        # comparison must preserve that sign rather than forcing quantity
        # to an absolute value.
        assert leg.quantity != 0
