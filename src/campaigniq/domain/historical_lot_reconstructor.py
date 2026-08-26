"""Reconstruct missing opening lots from historical evidence."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event import PositionEvent
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


class HistoricalLotReconstructor:
    """Reconstruct missing opening lots from historical evidence."""

    def __init__(
        self,
        *,
        campaign_reconstructor: CampaignReconstructor,
    ) -> None:
        self._campaign_reconstructor = campaign_reconstructor

    @staticmethod
    def seed_missing_option_lots(
        opening_lot_book: LotBook,
        historical_trades: tuple[Trade, ...],
    ) -> None:
        """Seed option lots missing from the opening snapshot when history is sufficient."""
        pending: dict[object, list[list[object]]] = {}
        invalid: set[object] = set()

        for trade in historical_trades:
            for leg in trade.legs:
                if not isinstance(leg.instrument, OptionContract):
                    continue
                instrument = leg.instrument
                if instrument in invalid:
                    continue
                quantity = sum(
                    (abs(execution.quantity) for execution in leg.executions),
                    Decimal("0"),
                )
                if quantity == 0:
                    continue
                lots = pending.setdefault(instrument, [])
                if leg.position_effect == PositionEffect.OPEN:
                    signed = quantity if leg.side == Side.BUY else -quantity
                    opened_at = min(
                        execution.executed_at for execution in leg.executions
                    )
                    lots.append([signed, opened_at])
                    continue
                target_sign = 1 if leg.side == Side.SELL else -1
                remaining = quantity
                for lot in lots:
                    if remaining <= 0:
                        break
                    lot_quantity = lot[0]
                    if (lot_quantity > 0) != (target_sign > 0):
                        continue
                    consumed = min(abs(lot_quantity), remaining)
                    lot[0] = (
                        lot_quantity - consumed
                        if lot_quantity > 0
                        else lot_quantity + consumed
                    )
                    remaining -= consumed
                if remaining:
                    invalid.add(instrument)

        for instrument, lots in pending.items():
            if instrument in invalid:
                continue

            remaining_lots = [
                (quantity, opened_at)
                for quantity, opened_at in lots
                if quantity != 0
            ]

            historical_quantity = sum(
                (quantity for quantity, _ in remaining_lots),
                Decimal("0"),
            )

            existing_quantity = sum(
                (lot.quantity for lot in opening_lot_book.lots(instrument)),
                Decimal("0"),
            )

            missing_quantity = historical_quantity - existing_quantity

            if missing_quantity == 0:
                continue

            if abs(missing_quantity) < abs(historical_quantity):
                opened_at = min(
                    opened_at
                    for quantity, opened_at in remaining_lots
                    if quantity != 0
                )
                opening_lot_book.seed(
                    Lot(
                        lot_id=(
                            f"HISTORICAL-TRADE:{opened_at.isoformat()}"
                            f":{instrument}:MISSING"
                        ),
                        instrument=instrument,
                        quantity=missing_quantity,
                        opened_at=opened_at,
                        basis_total=None,
                        basis_source="HISTORICAL_TRADE_RECONSTRUCTION",
                    )
                )
                continue

            for index, (quantity, opened_at) in enumerate(
                remaining_lots, 1
            ):
                opening_lot_book.seed(
                    Lot(
                        lot_id=(
                            f"HISTORICAL-TRADE:{opened_at.isoformat()}"
                            f":{instrument}:{index}"
                        ),
                        instrument=instrument,
                        quantity=quantity,
                        opened_at=opened_at,
                        basis_total=None,
                        basis_source="HISTORICAL_TRADE_RECONSTRUCTION",
                    )
                )

    @staticmethod
    def seed_missing_expiration_lots(
        opening_lot_book: LotBook,
        historical_expiration_events: list[PositionEvent],
        campaigns: tuple,
    ) -> None:
        """Seed missing equity lots created by historical expiration events."""
        required: dict[object, Decimal] = {}

        for campaign in campaigns:
            for trade in campaign.trades:
                for leg in trade.legs:
                    if leg.position_effect != PositionEffect.CLOSE:
                        continue

                    if not isinstance(leg.instrument, Instrument):
                        continue

                    quantity = sum(
                        (
                            abs(execution.quantity)
                            for execution in leg.executions
                        ),
                        Decimal("0"),
                    )

                    if quantity:
                        required[leg.instrument] = (
                            required.get(leg.instrument, Decimal("0"))
                            + quantity
                        )

        if not required:
            return

        for event in historical_expiration_events:
            for change in event.changes:
                if change.quantity <= 0:
                    continue

                instrument = change.instrument
                if not isinstance(instrument, Instrument):
                    continue

                if instrument not in required:
                    continue

                existing_quantity = sum(
                    (
                        lot.quantity
                        for lot in opening_lot_book.lots(instrument)
                        if lot.quantity > 0
                    ),
                    Decimal("0"),
                )

                missing_quantity = required[instrument] - existing_quantity
                if missing_quantity <= 0:
                    continue

                quantity = min(change.quantity, missing_quantity)

                opening_lot_book.seed(
                    Lot(
                        lot_id=(
                            f"HISTORICAL-EXPIRATION:"
                            f"{event.occurred_at.isoformat()}:"
                            f"{instrument}"
                        ),
                        instrument=instrument,
                        quantity=quantity,
                        opened_at=event.occurred_at,
                        basis_total=None,
                        basis_source="HISTORICAL_EXPIRATION_RECONSTRUCTION",
                    )
                )

    def seed_missing_covered_equity_lots(
        self,
        opening_lot_book: LotBook,
        campaigns: tuple,
        historical_trades: tuple[Trade, ...],
    ) -> None:
        """Reconstruct equity consumed by a continuing covered-call campaign."""
        if not historical_trades:
            return

        historical_campaigns = self._campaign_reconstructor.reconstruct(
            list(historical_trades)
        )

        historical_survivors: dict[OptionContract, Decimal] = defaultdict(
            Decimal
        )

        for historical_campaign in historical_campaigns:
            for trade in historical_campaign.trades:
                for leg in trade.legs:
                    if not isinstance(leg.instrument, OptionContract):
                        continue

                    for execution in leg.executions:
                        historical_survivors[leg.instrument] += (
                            execution.quantity
                        )

        for campaign in campaigns:
            closing_options: dict[OptionContract, Decimal] = defaultdict(
                Decimal
            )
            closing_equity: dict[Instrument, Decimal] = defaultdict(
                Decimal
            )

            for trade in campaign.trades:
                for leg in trade.legs:
                    if leg.position_effect != PositionEffect.CLOSE:
                        continue

                    quantity = sum(
                        (
                            abs(execution.quantity)
                            for execution in leg.executions
                        ),
                        Decimal("0"),
                    )

                    if quantity == 0:
                        continue

                    if isinstance(leg.instrument, OptionContract):
                        closing_options[leg.instrument] += quantity
                    elif isinstance(leg.instrument, Instrument):
                        closing_equity[leg.instrument] += quantity

            if not closing_options or not closing_equity:
                continue

            for option, closing_option_quantity in closing_options.items():
                historical_quantity = historical_survivors.get(
                    option,
                    Decimal("0"),
                )

                if historical_quantity >= 0:
                    continue

                historical_contracts = abs(historical_quantity)
                if historical_contracts <= closing_option_quantity:
                    continue

                opening_option_quantity = sum(
                    (
                        lot.quantity
                        for lot in opening_lot_book.lots(option)
                    ),
                    Decimal("0"),
                )

                if opening_option_quantity != historical_quantity:
                    continue

                underlying = Instrument(option.underlying)
                equity_close_quantity = closing_equity.get(
                    underlying,
                    Decimal("0"),
                )

                if equity_close_quantity <= 0:
                    continue

                opening_equity_lots = [
                    lot
                    for lot in opening_lot_book.lots(underlying)
                    if lot.quantity > 0 and lot.campaign_id is None
                ]

                if len(opening_equity_lots) != 1:
                    continue

                surviving_equity_lot = opening_equity_lots[0]

                if surviving_equity_lot.quantity >= equity_close_quantity:
                    continue

                missing_equity_quantity = equity_close_quantity

                historical_campaign_id = None
                for historical_campaign in historical_campaigns:
                    historical_quantity_for_option = sum(
                        (
                            execution.quantity
                            for trade in historical_campaign.trades
                            for leg in trade.legs
                            if (
                                leg.instrument == option
                                and leg.position_effect
                                == PositionEffect.OPEN
                            )
                            for execution in leg.executions
                        ),
                        Decimal("0"),
                    )

                    if historical_quantity_for_option == historical_quantity:
                        if historical_campaign_id is not None:
                            historical_campaign_id = None
                            break
                        historical_campaign_id = (
                            f"HIST-{historical_campaign.campaign_id}"
                        )

                if historical_campaign_id is None:
                    continue

                opening_lot_book.assign_campaign(
                    surviving_equity_lot.lot_id,
                    historical_campaign_id,
                )

                opened_at = surviving_equity_lot.opened_at
                opening_lot_book.seed(
                    Lot(
                        lot_id=(
                            f"HISTORICAL-COVERED-EQUITY:"
                            f"{opened_at.isoformat()}:"
                            f"{underlying}:"
                            f"{historical_campaign_id}"
                        ),
                        instrument=underlying,
                        quantity=missing_equity_quantity,
                        opened_at=opened_at,
                        basis_total=None,
                        basis_source="HISTORICAL_TRADE_RECONSTRUCTION",
                    )
                )
