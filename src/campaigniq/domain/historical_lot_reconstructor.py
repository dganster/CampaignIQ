"""Reconstruct missing opening lots from historical evidence."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
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
        historical_expiration_events: list[PositionEvent] | tuple[PositionEvent, ...] = (),
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
                    (
                        abs(execution.quantity)
                        for execution in leg.executions
                    ),
                    Decimal("0"),
                )

                if quantity == 0:
                    continue

                lots = pending.setdefault(instrument, [])

                if leg.position_effect == PositionEffect.OPEN:
                    signed = (
                        quantity
                        if leg.side == Side.BUY
                        else -quantity
                    )

                    opened_at = min(
                        execution.executed_at
                        for execution in leg.executions
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

            # TOS records an assigned short call expiration as the resulting
            # negative equity movement.  Such a call is no longer opening inventory.
            if (
                isinstance(instrument, OptionContract)
                and instrument.option_type == OptionType.CALL
                and any(
                    event.occurred_at > opened_at
                    and any(
                        isinstance(change.instrument, Instrument)
                        and change.instrument.symbol == instrument.underlying
                        and change.quantity < 0
                        for change in event.changes
                    )
                    for _, opened_at in remaining_lots
                    for event in historical_expiration_events
                )
            ):
                continue

            historical_quantity = sum(
                (
                    quantity
                    for quantity, _ in remaining_lots
                ),
                Decimal("0"),
            )

            existing_quantity = sum(
                (
                    lot.quantity
                    for lot in opening_lot_book.lots(instrument)
                ),
                Decimal("0"),
            )

            missing_quantity = (
                historical_quantity - existing_quantity
            )

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
                            f"HISTORICAL-TRADE:"
                            f"{opened_at.isoformat()}"
                            f":{instrument}:MISSING"
                        ),
                        instrument=instrument,
                        quantity=missing_quantity,
                        opened_at=opened_at,
                        basis_total=None,
                        basis_source=(
                            "HISTORICAL_TRADE_RECONSTRUCTION"
                        ),
                    )
                )

                continue

            for index, (quantity, opened_at) in enumerate(
                remaining_lots,
                1,
            ):
                opening_lot_book.seed(
                    Lot(
                        lot_id=(
                            f"HISTORICAL-TRADE:"
                            f"{opened_at.isoformat()}"
                            f":{instrument}:{index}"
                        ),
                        instrument=instrument,
                        quantity=quantity,
                        opened_at=opened_at,
                        basis_total=None,
                        basis_source=(
                            "HISTORICAL_TRADE_RECONSTRUCTION"
                        ),
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

                    if not isinstance(
                        leg.instrument,
                        Instrument,
                    ):
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
                            required.get(
                                leg.instrument,
                                Decimal("0"),
                            )
                            + quantity
                        )

        if not required:
            return

        for event in historical_expiration_events:
            for change in event.changes:
                if change.quantity <= 0:
                    continue

                instrument = change.instrument

                if not isinstance(
                    instrument,
                    Instrument,
                ):
                    continue

                if instrument not in required:
                    continue

                existing_quantity = sum(
                    (
                        lot.quantity
                        for lot in opening_lot_book.lots(
                            instrument
                        )
                        if lot.quantity > 0
                    ),
                    Decimal("0"),
                )

                missing_quantity = (
                    required[instrument]
                    - existing_quantity
                )

                if missing_quantity <= 0:
                    continue

                quantity = min(
                    change.quantity,
                    missing_quantity,
                )

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
                        basis_source=(
                            "HISTORICAL_EXPIRATION_RECONSTRUCTION"
                        ),
                    )
                )

    def assign_rolled_covered_equity_provenance(
        self,
        opening_lot_book: LotBook,
        campaigns: tuple,
        historical_trades: tuple[Trade, ...],
    ) -> None:
        """Assign proven covered equity to a continuing call-roll campaign."""
        if not historical_trades:
            return

        historical_campaigns = (
            self._campaign_reconstructor.reconstruct(
                list(historical_trades)
            )
        )

        for campaign in campaigns:
            closing_calls: dict[
                OptionContract,
                Decimal,
            ] = defaultdict(Decimal)

            opening_calls: dict[
                OptionContract,
                Decimal,
            ] = defaultdict(Decimal)

            for trade in campaign.trades:
                for leg in trade.legs:
                    if not isinstance(
                        leg.instrument,
                        OptionContract,
                    ):
                        continue

                    if leg.instrument.option_type != OptionType.CALL:
                        continue

                    quantity = sum(
                        (
                            abs(execution.quantity)
                            for execution in leg.executions
                        ),
                        Decimal("0"),
                    )

                    if not quantity:
                        continue

                    if (
                        leg.position_effect
                        == PositionEffect.CLOSE
                    ):
                        closing_calls[
                            leg.instrument
                        ] += quantity

                    elif (
                        leg.position_effect
                        == PositionEffect.OPEN
                        and leg.side == Side.SELL
                    ):
                        opening_calls[
                            leg.instrument
                        ] += quantity

            if not closing_calls or not opening_calls:
                continue

            for option, closing_quantity in closing_calls.items():
                historical_quantity = sum(
                    (
                        execution.quantity
                        for historical_campaign
                        in historical_campaigns
                        for trade in historical_campaign.trades
                        for leg in trade.legs
                        if leg.instrument == option
                        for execution in leg.executions
                    ),
                    Decimal("0"),
                )

                if historical_quantity >= 0:
                    continue

                if abs(historical_quantity) != closing_quantity:
                    continue

                opening_option_quantity = sum(
                    (
                        lot.quantity
                        for lot in opening_lot_book.lots(
                            option
                        )
                    ),
                    Decimal("0"),
                )

                if opening_option_quantity != historical_quantity:
                    continue

                underlying = Instrument(
                    option.underlying
                )

                replacement_quantity = sum(
                    (
                        quantity
                        for replacement, quantity
                        in opening_calls.items()
                        if (
                            replacement.underlying
                            == option.underlying
                        )
                    ),
                    Decimal("0"),
                )

                if replacement_quantity != closing_quantity:
                    continue

                positive_equity_lots = [
                    lot
                    for lot in opening_lot_book.lots(
                        underlying
                    )
                    if lot.quantity > 0
                ]

                if not positive_equity_lots:
                    continue

                required_equity_quantity = (
                    closing_quantity
                    * Decimal("100")
                )

                opening_equity_quantity = sum(
                    (
                        lot.quantity
                        for lot in positive_equity_lots
                    ),
                    Decimal("0"),
                )

                if (
                    opening_equity_quantity
                    != required_equity_quantity
                ):
                    continue

                historical_campaign_id = (
                    self._historical_campaign_id_for_option(
                        historical_campaigns,
                        option,
                        historical_quantity,
                    )
                )

                if historical_campaign_id is None:
                    continue

                historical_campaign = next(
                    (
                        historical_campaign
                        for historical_campaign
                        in historical_campaigns
                        if (
                            f"HIST-{historical_campaign.campaign_id}"
                            == historical_campaign_id
                        )
                    ),
                    None,
                )

                if historical_campaign is None:
                    continue

                historical_equity_quantity = Decimal("0")

                for historical_trade in historical_campaign.trades:
                    opens_rolled_option = any(
                        historical_leg.instrument == option
                        and historical_leg.position_effect
                        == PositionEffect.OPEN
                        and historical_leg.side == Side.SELL
                        for historical_leg in historical_trade.legs
                    )

                    if not opens_rolled_option:
                        continue

                    for historical_leg in historical_trade.legs:
                        if historical_leg.instrument != underlying:
                            continue

                        if (
                            historical_leg.position_effect
                            != PositionEffect.OPEN
                        ):
                            continue

                        quantity = sum(
                            (
                                abs(execution.quantity)
                                for execution
                                in historical_leg.executions
                            ),
                            Decimal("0"),
                        )

                        if historical_leg.side == Side.BUY:
                            historical_equity_quantity += quantity
                        else:
                            historical_equity_quantity -= quantity

                if (
                    historical_equity_quantity
                    != required_equity_quantity
                ):
                    continue

                for lot in positive_equity_lots:
                    if lot.campaign_id is not None:
                        opening_lot_book.reassign_campaign(
                            lot.lot_id,
                            campaign.campaign_id,
                        )
                    else:
                        opening_lot_book.assign_campaign(
                            lot.lot_id,
                            campaign.campaign_id,
                        )

    def seed_missing_covered_equity_lots(
        self,
        opening_lot_book: LotBook,
        campaigns: tuple,
        historical_trades: tuple[Trade, ...],
    ) -> None:
        """Reconstruct equity consumed by continuing covered-call campaigns.

        Historical trade evidence can establish that an option and its
        underlying shares belonged to the same covered-call campaign before
        the selected period.

        This includes the case where the entire historical short option
        position is closed during the current period.  The historical option
        does not need to survive beyond the current period in order to prove
        the ancestry of the underlying equity.

        Example:

            July:
                SELL 1 LMT Aug 21 480 CALL
                BUY 100 LMT

            August:
                BUY 1 LMT Aug 21 480 CALL
                SELL 100 LMT

        The July history establishes the provenance of the 100 LMT shares.
        If the August opening snapshot does not contain LMT shares, reconstruct
        that 100-share lot from the July evidence.
        """
        if not historical_trades:
            return

        historical_campaigns = (
            self._campaign_reconstructor.reconstruct(
                list(historical_trades)
            )
        )

        historical_survivors: dict[
            OptionContract,
            Decimal,
        ] = defaultdict(Decimal)

        for historical_campaign in historical_campaigns:
            for trade in historical_campaign.trades:
                for leg in trade.legs:
                    if not isinstance(
                        leg.instrument,
                        OptionContract,
                    ):
                        continue

                    for execution in leg.executions:
                        historical_survivors[
                            leg.instrument
                        ] += execution.quantity

        for campaign in campaigns:
            closing_options: dict[
                OptionContract,
                Decimal,
            ] = defaultdict(Decimal)

            closing_equity: dict[
                Instrument,
                Decimal,
            ] = defaultdict(Decimal)

            for trade in campaign.trades:
                for leg in trade.legs:
                    if (
                        leg.position_effect
                        != PositionEffect.CLOSE
                    ):
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

                    if isinstance(
                        leg.instrument,
                        OptionContract,
                    ):
                        closing_options[
                            leg.instrument
                        ] += quantity

                    elif isinstance(
                        leg.instrument,
                        Instrument,
                    ):
                        closing_equity[
                            leg.instrument
                        ] += quantity

            if not closing_options or not closing_equity:
                continue

            for (
                option,
                closing_option_quantity,
            ) in closing_options.items():

                historical_quantity = (
                    historical_survivors.get(
                        option,
                        Decimal("0"),
                    )
                )

                # We need a historical short option position.
                if historical_quantity >= 0:
                    continue

                historical_contracts = abs(
                    historical_quantity
                )

                # IMPORTANT:
                #
                # Equality is valid.
                #
                # A historical short call of -1 contract that is
                # completely closed by a +1-contract current-period
                # close is still sufficient evidence that the
                # underlying shares originated with that historical
                # covered-call campaign.
                #
                # The old <= comparison incorrectly rejected exactly
                # this case.
                if historical_contracts < closing_option_quantity:
                    continue

                opening_option_quantity = sum(
                    (
                        lot.quantity
                        for lot in opening_lot_book.lots(
                            option
                        )
                    ),
                    Decimal("0"),
                )

                if opening_option_quantity != historical_quantity:
                    continue

                underlying = Instrument(
                    option.underlying
                )

                equity_close_quantity = (
                    closing_equity.get(
                        underlying,
                        Decimal("0"),
                    )
                )

                if equity_close_quantity <= 0:
                    continue

                historical_campaign_id = (
                    self._historical_campaign_id_for_option(
                        historical_campaigns,
                        option,
                        historical_quantity,
                    )
                )

                if historical_campaign_id is None:
                    continue

                opening_equity_lots = [
                    lot
                    for lot in opening_lot_book.lots(
                        underlying
                    )
                    if lot.quantity > 0
                ]

                existing_quantity = sum(
                    (
                        lot.quantity
                        for lot in opening_equity_lots
                    ),
                    Decimal("0"),
                )

                if existing_quantity >= equity_close_quantity:
                    continue

                missing_equity_quantity = (
                    equity_close_quantity
                    - existing_quantity
                )

                if opening_equity_lots:
                    opened_at = (
                        opening_equity_lots[0].opened_at
                    )

                else:
                    opened_at = (
                        self._historical_option_opened_at(
                            historical_campaigns,
                            option,
                        )
                    )

                    if opened_at is None:
                        opened_at = min(
                            execution.executed_at
                            for trade in campaign.trades
                            for leg in trade.legs
                            if (
                                leg.instrument
                                == underlying
                                and leg.position_effect
                                == PositionEffect.CLOSE
                            )
                            for execution in leg.executions
                        )

                lot_id = (
                    f"HISTORICAL-COVERED-EQUITY:"
                    f"{opened_at.isoformat()}:"
                    f"{underlying}:"
                    f"{historical_campaign_id}"
                )

                if any(
                    lot.lot_id == lot_id
                    for lot in opening_lot_book.lots(
                        underlying
                    )
                ):
                    continue

                opening_lot_book.seed(
                    Lot(
                        lot_id=lot_id,
                        instrument=underlying,
                        quantity=missing_equity_quantity,
                        opened_at=opened_at,
                        basis_total=None,
                        basis_source=(
                            "HISTORICAL_TRADE_RECONSTRUCTION"
                        ),
                    )
                )

    def assign_assignment_option_provenance(
        self,
        opening_lot_book: LotBook,
        assignment_events: tuple[PositionEvent, ...],
        historical_trades: tuple[Trade, ...],
)     ->None:
        """Assign historical provenance needed by current-period assignments."""
        if not historical_trades or not assignment_events:
            return

        historical_campaigns = (
            self._campaign_reconstructor.reconstruct(
                list(historical_trades)
            )
        )

        for event in assignment_events:
            for change in event.changes:
                if not isinstance(
                    change.instrument,
                    OptionContract,
                ):
                    continue

                # A positive option change closes a historical short option.
                if change.quantity <= 0:
                    continue

                option = change.instrument

                opening_lots = [
                    lot
                    for lot in opening_lot_book.lots(option)
                    if lot.campaign_id is None
                ]

                if not opening_lots:
                    continue

                opening_quantity = sum(
                    (lot.quantity for lot in opening_lots),
                    Decimal("0"),
                )

                # Assignment of a short call requires a negative opening
                # option position of at least the assigned quantity.
                if opening_quantity >= 0:
                    continue

                if abs(opening_quantity) < abs(change.quantity):
                    continue

                historical_campaign_id = (
                    self._historical_campaign_id_for_option(
                        historical_campaigns,
                        option,
                        opening_quantity,
                    )
                )

                if historical_campaign_id is None:
                    continue

                remaining = abs(change.quantity)

                for lot in opening_lots:
                    if remaining <= 0:
                        break

                    if lot.quantity >= 0:
                        continue

                    lot_quantity = min(
                        abs(lot.quantity),
                        remaining,
                    )

                    if lot_quantity != abs(lot.quantity):
                        # Do not partially assign provenance to a lot.
                        # Ambiguous lot splitting should remain unresolved.
                        return

                    opening_lot_book.assign_campaign(
                        lot.lot_id,
                        historical_campaign_id,
                    )

                    remaining -= lot_quantity

    def assign_boundary_assignment_covered_equity_provenance(
        self,
        opening_lot_book: LotBook,
        boundary_assignment_events: tuple[PositionEvent, ...],
        historical_trades: tuple[Trade, ...],
    ) -> None:
        """Assign historical provenance to exact covered equity at a boundary.

        Boundary assignment evidence may explain an opening covered-call
        position without becoming a position event for the selected period.
        Provenance is assigned only when the entire positive opening equity
        position exactly matches the assignment quantity and is otherwise
        unassigned. Anything ambiguous remains unresolved.
        """
        if not historical_trades or not boundary_assignment_events:
            return

        historical_campaigns = (
            self._campaign_reconstructor.reconstruct(
                list(historical_trades)
            )
        )

        historical_survivors: dict[
            OptionContract,
            Decimal,
        ] = defaultdict(Decimal)

        for historical_campaign in historical_campaigns:
            for trade in historical_campaign.trades:
                for leg in trade.legs:
                    if not isinstance(
                        leg.instrument,
                        OptionContract,
                    ):
                        continue

                    for execution in leg.executions:
                        historical_survivors[
                            leg.instrument
                        ] += execution.quantity

        for event in boundary_assignment_events:
            option_changes = [
                change
                for change in event.changes
                if (
                    isinstance(change.instrument, OptionContract)
                    and change.quantity > 0
                )
            ]

            equity_changes = [
                change
                for change in event.changes
                if (
                    isinstance(change.instrument, Instrument)
                    and change.quantity < 0
                )
            ]

            for option_change in option_changes:
                option = option_change.instrument
                underlying = Instrument(option.underlying)

                equity_close_quantity = sum(
                    (
                        abs(change.quantity)
                        for change in equity_changes
                        if change.instrument == underlying
                    ),
                    Decimal("0"),
                )

                if equity_close_quantity <= 0:
                    continue

                historical_quantity = historical_survivors.get(
                    option,
                    Decimal("0"),
                )

                if historical_quantity >= 0:
                    continue

                if (
                    abs(historical_quantity)
                    < abs(option_change.quantity)
                ):
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

                historical_campaign_id = (
                    self._historical_campaign_id_for_option(
                        historical_campaigns,
                        option,
                        historical_quantity,
                    )
                )

                if historical_campaign_id is None:
                    continue

                positive_equity_lots = [
                    lot
                    for lot in opening_lot_book.lots(underlying)
                    if lot.quantity > 0
                ]

                if not positive_equity_lots:
                    continue

                if any(
                    lot.campaign_id is not None
                    for lot in positive_equity_lots
                ):
                    continue

                opening_equity_quantity = sum(
                    (
                        lot.quantity
                        for lot in positive_equity_lots
                    ),
                    Decimal("0"),
                )

                if opening_equity_quantity != equity_close_quantity:
                    continue

                for lot in positive_equity_lots:
                    opening_lot_book.assign_campaign(
                        lot.lot_id,
                        historical_campaign_id,
                    )

    def seed_missing_assignment_covered_equity_lots(
        self,
        opening_lot_book: LotBook,
        assignment_events: tuple[PositionEvent, ...],
        historical_trades: tuple[Trade, ...],
    ) -> None:
        """Reconstruct covered equity consumed by option assignments."""
        if not historical_trades or not assignment_events:
            return

        historical_campaigns = (
            self._campaign_reconstructor.reconstruct(
                list(historical_trades)
            )
        )

        historical_survivors: dict[
            OptionContract,
            Decimal,
        ] = defaultdict(Decimal)

        for historical_campaign in historical_campaigns:
            for trade in historical_campaign.trades:
                for leg in trade.legs:
                    if not isinstance(
                        leg.instrument,
                        OptionContract,
                    ):
                        continue

                    for execution in leg.executions:
                        historical_survivors[
                            leg.instrument
                        ] += execution.quantity

        for event in assignment_events:
            option_changes = [
                change
                for change in event.changes
                if (
                    isinstance(change.instrument, OptionContract)
                    and change.quantity > 0
                )
            ]

            equity_changes = [
                change
                for change in event.changes
                if (
                    isinstance(change.instrument, Instrument)
                    and change.quantity < 0
                )
            ]

            for option_change in option_changes:
                option = option_change.instrument

                underlying = Instrument(option.underlying)

                equity_close_quantity = sum(
                    (
                        abs(change.quantity)
                        for change in equity_changes
                        if change.instrument == underlying
                    ),
                    Decimal("0"),
                )

                if equity_close_quantity <= 0:
                    continue

                historical_quantity = (
                    historical_survivors.get(
                        option,
                        Decimal("0"),
                    )
                )

                if historical_quantity >= 0:
                    continue

                closing_option_quantity = abs(
                    option_change.quantity
                )

                if (
                    abs(historical_quantity)
                    < closing_option_quantity
                ):
                    continue

                opening_option_quantity = sum(
                    (
                        lot.quantity
                        for lot in opening_lot_book.lots(
                            option
                        )
                    ),
                    Decimal("0"),
                )

                if opening_option_quantity != historical_quantity:
                    continue

                historical_campaign_id = (
                    self._historical_campaign_id_for_option(
                        historical_campaigns,
                        option,
                        historical_quantity,
                    )
                )

                if historical_campaign_id is None:
                    continue

                opening_equity_lots = [
                    lot
                    for lot in opening_lot_book.lots(
                        underlying
                    )
                    if lot.quantity > 0
                ]

                existing_quantity = sum(
                    (
                        lot.quantity
                        for lot in opening_equity_lots
                    ),
                    Decimal("0"),
                )

                if existing_quantity == equity_close_quantity:
                    for lot in opening_equity_lots:
                        if lot.campaign_id is not None:
                            opening_lot_book.reassign_campaign(
                                lot.lot_id,
                                historical_campaign_id,
                            )
                        else:
                            opening_lot_book.assign_campaign(
                                lot.lot_id,
                                historical_campaign_id,
                            )
                    continue

                if existing_quantity > equity_close_quantity:
                    continue

                missing_equity_quantity = (
                    equity_close_quantity
                    - existing_quantity
                )

                if opening_equity_lots:
                    opened_at = opening_equity_lots[0].opened_at
                else:
                    opened_at = (
                        self._historical_option_opened_at(
                            historical_campaigns,
                            option,
                        )
                    )

                if opened_at is None:
                    opened_at = event.occurred_at

                lot_id = (
                    f"HISTORICAL-COVERED-EQUITY:"
                    f"{opened_at.isoformat()}:"
                    f"{underlying}:"
                    f"{historical_campaign_id}"
                )

                if any(
                    lot.lot_id == lot_id
                    for lot in opening_lot_book.lots(
                        underlying
                    )
                ):
                    continue

                opening_lot_book.seed(
                    Lot(
                        lot_id=lot_id,
                        instrument=underlying,
                        quantity=missing_equity_quantity,
                        opened_at=opened_at,
                        basis_total=None,
                        basis_source=(
                            "HISTORICAL_TRADE_RECONSTRUCTION"
                        ),
                    )
                )

    @staticmethod
    def _historical_campaign_id_for_option(
        historical_campaigns: tuple,
        option: OptionContract,
        historical_quantity: Decimal,
    ) -> str | None:
        """Return the unique historical campaign carrying the option."""
        matched_campaign_id: str | None = None

        for historical_campaign in historical_campaigns:
            quantity = sum(
                (
                    execution.quantity
                    for trade in historical_campaign.trades
                    for leg in trade.legs
                    if leg.instrument == option
                    for execution in leg.executions
                ),
                Decimal("0"),
            )

            if quantity != historical_quantity:
                continue

            candidate = (
                f"HIST-{historical_campaign.campaign_id}"
            )

            if matched_campaign_id is not None:
                # Ambiguous ancestry: do not invent provenance.
                return None

            matched_campaign_id = candidate

        return matched_campaign_id

    @staticmethod
    def _historical_option_opened_at(
        historical_campaigns: tuple,
        option: OptionContract,
    ) -> datetime | None:
        """Return earliest historical opening execution for an option."""
        opened_at: datetime | None = None

        for historical_campaign in historical_campaigns:
            for trade in historical_campaign.trades:
                for leg in trade.legs:
                    if (
                        leg.instrument != option
                        or leg.position_effect
                        != PositionEffect.OPEN
                    ):
                        continue

                    for execution in leg.executions:
                        if (
                            opened_at is None
                            or execution.executed_at
                            < opened_at
                        ):
                            opened_at = execution.executed_at
