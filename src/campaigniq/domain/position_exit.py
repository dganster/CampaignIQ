from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


PositionInstrument = Instrument | OptionContract
PositionSnapshot = tuple[tuple[PositionInstrument, Decimal], ...]


@dataclass(frozen=True, slots=True)
class PositionExit:
    underlying: str
    trade: Trade
    before_positions: PositionSnapshot
    after_positions: PositionSnapshot


def _symbol(instrument: PositionInstrument) -> str:
    if isinstance(instrument, OptionContract):
        return instrument.underlying.upper()
    return instrument.symbol.upper()


def _trade_underlyings(trade: Trade) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                _symbol(leg.instrument)
                for leg in trade.legs
            }
        )
    )


def _positions_for_underlying(
    book: LotBook,
    underlying: str,
) -> PositionSnapshot:
    positions: list[tuple[PositionInstrument, Decimal]] = []

    for instrument in book.instruments():
        if _symbol(instrument) != underlying:
            continue

        quantity = sum(
            (lot.quantity for lot in book.lots(instrument)),
            Decimal("0"),
        )

        if quantity != 0:
            positions.append((instrument, quantity))

    return tuple(
        sorted(
            positions,
            key=lambda item: repr(item[0]),
        )
    )


def _available_close_quantity(
    book: LotBook,
    instrument: PositionInstrument,
    side: Side,
) -> Decimal:
    lots = book.lots(instrument)

    if side is Side.SELL:
        return sum(
            (lot.quantity for lot in lots if lot.quantity > 0),
            Decimal("0"),
        )

    return sum(
        (-lot.quantity for lot in lots if lot.quantity < 0),
        Decimal("0"),
    )


def _trade_is_supported_by_opening_state(
    book: LotBook,
    trade: Trade,
) -> bool:
    """
    Require aggregate CLOSE demand to be supported by authoritative lots.

    Multiple CLOSE legs for the same instrument and side are evaluated
    together so individually valid legs cannot overconsume opening inventory.
    """
    required: dict[
        tuple[PositionInstrument, Side],
        Decimal,
    ] = {}

    for leg in trade.legs:
        if leg.position_effect is not PositionEffect.CLOSE:
            continue

        quantity = abs(
            sum(
                (
                    execution.quantity
                    for execution in leg.executions
                ),
                Decimal("0"),
            )
        )

        if quantity == 0:
            continue

        key = (leg.instrument, leg.side)
        required[key] = (
            required.get(key, Decimal("0"))
            + quantity
        )

    for (instrument, side), quantity in required.items():
        available = _available_close_quantity(
            book,
            instrument,
            side,
        )

        if available < quantity:
            return False

    return True


def detect_position_exit(
    *,
    opening_lot_book: LotBook,
    trade: Trade,
) -> tuple[PositionExit, ...]:
    """
    Detect complete exits from authoritative known position state.

    An EXIT is emitted only when:
      * the underlying has known non-zero exposure before the trade,
      * every close in the trade is supported by that opening state, and
      * no exposure for that underlying remains after the trade.

    Partial closes, rolls, and trades that leave any stock or option
    exposure behind are not exits.
    """
    underlyings = _trade_underlyings(trade)
    if not underlyings:
        return ()

    before_by_underlying = {
        underlying: _positions_for_underlying(
            opening_lot_book,
            underlying,
        )
        for underlying in underlyings
    }

    if not any(before_by_underlying.values()):
        return ()

    if not _trade_is_supported_by_opening_state(
        opening_lot_book,
        trade,
    ):
        return ()

    after = opening_lot_book.clone()
    after.apply_trade(trade)

    exits = []

    for underlying in underlyings:
        before_positions = before_by_underlying[underlying]

        if not before_positions:
            continue

        after_positions = _positions_for_underlying(
            after,
            underlying,
        )

        if after_positions:
            continue

        exits.append(
            PositionExit(
                underlying=underlying,
                trade=trade,
                before_positions=before_positions,
                after_positions=after_positions,
            )
        )

    return tuple(exits)
